"""Ce qui part réellement sur le fil.

Le point le plus fragile du projet est le contournement de `temperature` : la
famille GPT-5 renvoie un 400 si on l'envoie, les familles antérieures en ont
besoin. Un transport factice permet de vérifier la requête émise sans clé ni
réseau.
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from src.api.schemas import AnswerPayload
from src.config import Settings
from src.llm.cache import LLMCache
from src.llm.openai_provider import OpenAIProvider

GROQ = "https://api.groq.com/openai/v1"


def _response_payload(text: str, *, model: str) -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": "completed",
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 400},
            "output_tokens": 200,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1200,
        },
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


class Recorder:
    """Transport factice : enregistre les requêtes, renvoie une réponse canonique."""

    def __init__(self, *, text: str = '{"answer": "ok", "confidence": 0.5}', model: str) -> None:
        self.requests: list[dict] = []
        self.urls: list[str] = []
        self._text = text
        self._model = model
        self.status = 200
        self.fail_times = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.urls.append(str(request.url))
        if self.fail_times > 0:
            self.fail_times -= 1
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=_response_payload(self._text, model=self._model))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def build(model: str, cache_path, recorder: Recorder, **kw) -> OpenAIProvider:
    return OpenAIProvider(
        model=model,
        cache=LLMCache(cache_path),
        http_client=recorder.client(),
        # Les tests vérifient la politique de reprise, pas la montre : sans
        # cela le backoff réel ajoute une dizaine de secondes à la suite.
        retry_wait_initial=kw.pop("retry_wait_initial", 0.0),
        **kw,
    )


MESSAGES = [{"role": "user", "content": "Poids de Dracaufeu ?"}]


# --- le piège temperature -------------------------------------------------


def test_gpt5_n_envoie_pas_temperature(tmp_cache_path):
    """L'API répond 400 « only the default (1) value is supported »."""
    rec = Recorder(model="gpt-5.6-luna")
    build("gpt-5.6-luna", tmp_cache_path, rec, reasoning_effort="medium").complete(MESSAGES)

    sent = rec.requests[0]
    assert "temperature" not in sent
    assert sent["reasoning"] == {"effort": "medium"}


def test_les_modeles_anterieurs_recoivent_bien_temperature_zero(tmp_cache_path):
    rec = Recorder(model="gpt-4.1-mini")
    build("gpt-4.1-mini", tmp_cache_path, rec).complete(MESSAGES)

    sent = rec.requests[0]
    assert sent["temperature"] == 0.0
    assert "reasoning" not in sent


def test_la_verbosite_n_est_pas_envoyee_avec_un_schema(tmp_cache_path):
    """`text_format` alimente déjà `text.format` ; un `text` à côté entrerait
    en conflit."""
    rec = Recorder(model="gpt-5.6-luna")
    provider = build("gpt-5.6-luna", tmp_cache_path, rec)

    provider.complete(MESSAGES)
    assert rec.requests[0]["text"] == {"verbosity": "low"}

    provider.complete([{"role": "user", "content": "autre"}], schema=AnswerPayload)
    assert rec.requests[1]["text"].get("verbosity") is None
    assert rec.requests[1]["text"]["format"]["type"] == "json_schema"


# --- passerelle compatible OpenAI ----------------------------------------


def test_un_modele_prefixe_par_l_editeur_garde_les_controles_de_raisonnement(tmp_cache_path):
    """Groq sert la famille gpt-oss sous « openai/gpt-oss-120b ».

    Sans découpage du préfixe éditeur, le modèle retomberait sur `temperature` —
    qu'il accepte, donc sans erreur : la bascule serait invisible, et le chiffre
    du jalon 2 aurait été mesuré avec d'autres réglages que ceux annoncés.
    """
    rec = Recorder(model="openai/gpt-oss-120b")
    build("openai/gpt-oss-120b", tmp_cache_path, rec, base_url=GROQ).complete(MESSAGES)

    sent = rec.requests[0]
    assert sent["reasoning"] == {"effort": "low"}
    assert "temperature" not in sent


def test_base_url_deroute_les_requetes_et_renomme_le_fournisseur(tmp_cache_path):
    rec = Recorder(model="openai/gpt-oss-120b")
    provider = build("openai/gpt-oss-120b", tmp_cache_path, rec, base_url=GROQ)
    provider.complete(MESSAGES)

    # `name` part dans /health : il doit dire où les requêtes vont vraiment.
    assert provider.name == "api.groq.com"
    assert rec.urls[0] == f"{GROQ}/responses"


def test_deux_endpoints_ne_partagent_pas_le_cache(tmp_cache_path):
    """Le même nom de modèle servi ailleurs n'est pas le même modèle."""
    rec = Recorder(model="openai/gpt-oss-120b")
    build("openai/gpt-oss-120b", tmp_cache_path, rec, base_url=GROQ).complete(MESSAGES)
    build("openai/gpt-oss-120b", tmp_cache_path, rec).complete(MESSAGES)

    assert len(rec.requests) == 2


def test_une_base_url_vide_vaut_non_renseignee():
    """`OPENAI_BASE_URL=` dans .env arrive comme chaîne vide ; le SDK OpenAI
    ne l'accepte pas comme valeur par défaut."""
    assert Settings(openai_base_url="").openai_base_url is None


# --- cache et coût --------------------------------------------------------


def test_le_second_appel_identique_ne_touche_pas_le_reseau(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna")
    provider = build("gpt-5.6-luna", tmp_cache_path, rec)

    first = provider.complete(MESSAGES)
    second = provider.complete(MESSAGES)

    assert len(rec.requests) == 1, "le second appel aurait dû être servi par le cache"
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.cost_usd > 0
    assert second.cost_usd == 0.0, "un service par le cache ne coûte rien"
    assert second.text == first.text


def test_le_cout_tient_compte_des_tokens_caches(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna")
    resp = build("gpt-5.6-luna", tmp_cache_path, rec).complete(MESSAGES)

    # 600 frais à 0,20 $/M + 400 cachés à 0,02 $/M + 200 sortie à 1,20 $/M
    attendu = (600 * 0.20 + 400 * 0.02 + 200 * 1.20) / 1_000_000
    assert resp.cost_usd == pytest.approx(attendu)
    assert resp.input_tokens == 1000
    assert resp.cached_input_tokens == 400


def test_changer_d_effort_invalide_le_cache(tmp_cache_path):
    """Sinon une éval relancée avec d'autres réglages rejouerait les anciennes
    réponses en silence."""
    rec = Recorder(model="gpt-5.6-luna")
    build("gpt-5.6-luna", tmp_cache_path, rec, reasoning_effort="low").complete(MESSAGES)
    build("gpt-5.6-luna", tmp_cache_path, rec, reasoning_effort="high").complete(MESSAGES)
    assert len(rec.requests) == 2


def test_le_journal_enregistre_les_deux_appels(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna")
    cache = LLMCache(tmp_cache_path)
    provider = OpenAIProvider(model="gpt-5.6-luna", cache=cache, http_client=rec.client())

    provider.complete(MESSAGES, run_label="jalon1")
    provider.complete(MESSAGES)

    rows = cache._conn.execute("SELECT cache_hit, run_label FROM llm_calls ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(0, "jalon1"), (1, None)]


# --- sortie structurée et reprise ----------------------------------------


def test_sortie_structuree_validee(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna", text='{"answer": "90,5 kg", "confidence": 0.7}')
    resp = build("gpt-5.6-luna", tmp_cache_path, rec).complete(MESSAGES, schema=AnswerPayload)

    assert isinstance(resp.parsed, AnswerPayload)
    assert resp.parsed.answer == "90,5 kg"


def test_le_cache_restitue_un_objet_pydantic_pas_un_dict(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna", text='{"answer": "90,5 kg", "confidence": 0.7}')
    provider = build("gpt-5.6-luna", tmp_cache_path, rec)

    provider.complete(MESSAGES, schema=AnswerPayload)
    depuis_cache = provider.complete(MESSAGES, schema=AnswerPayload)

    assert depuis_cache.cache_hit is True
    assert isinstance(depuis_cache.parsed, AnswerPayload)
    assert depuis_cache.parsed.answer == "90,5 kg"


def test_un_429_est_rejoue(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna")
    rec.fail_times = 2
    provider = build("gpt-5.6-luna", tmp_cache_path, rec, max_retries=5)

    resp = provider.complete(MESSAGES)
    assert len(rec.requests) == 3
    assert resp.text


def test_l_abandon_survient_apres_le_budget_de_tentatives(tmp_cache_path):
    rec = Recorder(model="gpt-5.6-luna")
    rec.fail_times = 99
    provider = build("gpt-5.6-luna", tmp_cache_path, rec, max_retries=3)

    with pytest.raises(openai.RateLimitError):
        provider.complete(MESSAGES)
    assert len(rec.requests) == 3
