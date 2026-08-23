from __future__ import annotations

import pytest

from src.api.schemas import AnswerPayload, PokemonFacts
from tests.conftest import FakeProvider

ANSWER = {"answer": "Dracaufeu pèse 90,5 kg.", "confidence": 0.82}
FACTS = {
    "name": "Dracaufeu",
    "national_dex_number": 6,
    "types": ["feu", "vol"],
    "base_stats": None,
    "evolves_from": "Reptincel",
    "confidence": 0.9,
}


def test_health_signale_la_base_joignable(make_client, monkeypatch):
    monkeypatch.setattr("src.api.routes.ping", lambda: True)
    body = make_client(FakeProvider()).get("/health").json()
    assert body == {
        "status": "ok",
        "db": "ok",
        "llm_provider": "fake",
        "model": "gpt-5.6-luna",
    }


def test_health_degrade_sans_base_mais_ne_plante_pas(make_client, monkeypatch):
    """La santé doit rester interrogeable quand Postgres est absent."""
    monkeypatch.setattr("src.api.routes.ping", lambda: False)
    resp = make_client(FakeProvider()).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["db"] == "unreachable"


def test_ask_renvoie_la_reponse_et_sa_consommation(make_client):
    resp = make_client(FakeProvider([ANSWER])).post("/ask", json={"question": "Poids ?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == ANSWER["answer"]
    assert body["confidence"] == pytest.approx(0.82)
    assert body["usage"]["cost_usd"] > 0
    assert body["usage"]["cache_hit"] is False


def test_ask_ne_cite_rien_au_jalon_1(make_client):
    """L'appel est nu : il n'y a aucune source à produire. Le champ existe
    pour que le gain du jalon 3 se lise dans un schéma inchangé."""
    body = make_client(FakeProvider([ANSWER])).post("/ask", json={"question": "Poids ?"}).json()
    assert body["sources"] == []


def test_ask_demande_bien_une_sortie_structuree(make_client):
    provider = FakeProvider([ANSWER])
    make_client(provider).post("/ask", json={"question": "Poids ?"})
    _messages, schema = provider.calls[0]
    assert schema is AnswerPayload


@pytest.mark.parametrize(
    ("persona", "attendu"),
    [("pokedex", "Pokédex"), ("factual", "strictement factuelle")],
)
def test_la_persona_change_l_invite_systeme(make_client, persona, attendu):
    provider = FakeProvider([ANSWER])
    make_client(provider).post("/ask", json={"question": "Poids ?", "persona": persona})
    messages, _ = provider.calls[0]
    assert messages[0]["role"] == "system"
    assert attendu in messages[0]["content"]


def test_persona_par_defaut_pokedex(make_client):
    provider = FakeProvider([ANSWER])
    make_client(provider).post("/ask", json={"question": "Poids ?"})
    assert "Pokédex" in provider.calls[0][0][0]["content"]


def test_question_vide_rejetee(make_client):
    assert make_client(FakeProvider()).post("/ask", json={"question": ""}).status_code == 422


def test_persona_inconnue_rejetee(make_client):
    resp = make_client(FakeProvider()).post("/ask", json={"question": "x", "persona": "pirate"})
    assert resp.status_code == 422


def test_extract_valide_la_sortie(make_client):
    provider = FakeProvider([FACTS])
    resp = make_client(provider).post("/extract", json={"text": "Il évolue de Reptincel."})
    assert resp.status_code == 200
    assert resp.json()["facts"] == FACTS
    assert provider.calls[0][1] is PokemonFacts


def test_un_refus_du_modele_devient_un_422_explicite(make_client):
    """Un refus arrive comme un contenu, pas comme une exception : l'ignorer
    produirait une réponse vide et silencieuse."""
    client = make_client(FakeProvider(refusal="contenu non conforme"))
    resp = client.post("/ask", json={"question": "..."})
    assert resp.status_code == 422
    assert "refusé" in resp.json()["detail"]


def test_sortie_non_conforme_au_schema_devient_un_502(make_client):
    resp = make_client(FakeProvider([])).post("/ask", json={"question": "Poids ?"})
    assert resp.status_code == 502
