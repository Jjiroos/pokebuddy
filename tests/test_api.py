from __future__ import annotations

import pytest

from src.api.schemas import AnswerPayload, PokemonFacts, RoutePlan, SqlQuery
from src.tools.sql import SqlRefused
from tests.conftest import FakeProvider

ANSWER = {"answer": "Dracaufeu pèse 90,5 kg.", "confidence": 0.82}
# Le routeur qui demande la base et rien d'autre : le chemin du jalon 3.
BESOIN_DB = {"needs_db": True, "lore_query": None, "species": None, "reason": "fait chiffré"}
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


def test_ask_ne_cite_rien_quand_le_sql_ne_s_applique_pas(make_client):
    """Sans requête exécutée, il n'y a rien à citer — et surtout rien à
    inventer dans `sources`."""
    body = make_client(FakeProvider([ANSWER])).post("/ask", json={"question": "Poids ?"}).json()
    assert body["sources"] == []


def test_ask_route_puis_redige_quand_aucun_outil_n_est_choisi(make_client):
    """Sans outil, le pipeline coûte deux appels : router, puis rédiger."""
    provider = FakeProvider([ANSWER])
    make_client(provider).post("/ask", json={"question": "Poids ?"})
    assert [schema for _messages, schema in provider.calls] == [RoutePlan, AnswerPayload]


@pytest.mark.parametrize(
    ("persona", "attendu"),
    [("pokedex", "Pokédex"), ("factual", "strictement factuelle")],
)
def test_la_persona_change_l_invite_systeme(make_client, persona, attendu):
    provider = FakeProvider([ANSWER])
    make_client(provider).post("/ask", json={"question": "Poids ?", "persona": persona})
    # Le routeur ne porte aucune persona : c'est le dernier appel, celui qui
    # rédige, qui doit la porter.
    messages, _ = provider.calls[-1]
    assert messages[0]["role"] == "system"
    assert attendu in messages[0]["content"]


def test_persona_par_defaut_pokedex(make_client):
    provider = FakeProvider([ANSWER])
    make_client(provider).post("/ask", json={"question": "Poids ?"})
    assert "Pokédex" in provider.calls[-1][0][0]["content"]


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
    assert provider.calls[0][1] is PokemonFacts  # /extract reste un seul appel


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


def test_une_cle_invalide_donne_un_message_actionnable(make_client):
    """Une clé absente fait échouer le démarrage ; une clé invalide ne se
    découvre qu'au premier appel et ne doit pas ressortir en 500 opaque."""
    import httpx
    import openai

    class Rejeteur(FakeProvider):
        def complete(self, messages, *, schema=None, run_label=None):
            raise openai.AuthenticationError(
                "clé invalide",
                response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                body=None,
            )

    client = make_client(Rejeteur())
    client.raise_server_exceptions = False
    resp = client.post("/ask", json={"question": "Poids ?"})
    assert resp.status_code == 503
    assert "OPENAI_API_KEY" in resp.json()["detail"]


# --- le chemin outillé ----------------------------------------------------


def test_ask_execute_le_sql_et_cite_la_requete(make_client, monkeypatch):
    """Le chemin nominal du jalon 3, sans base : `run_query` est doublé.

    `sources` doit porter la requête **réellement exécutée** — celle que
    l'outil a réécrite, LIMIT compris — et non celle proposée par le modèle.
    """
    executee = "SELECT name FROM pokemon WHERE speed > 130 LIMIT 200"
    monkeypatch.setattr(
        "src.agent.graph.run_query",
        lambda sql: ([{"name": "barraskewda"}], executee),
    )
    provider = FakeProvider(
        [ANSWER],
        route=BESOIN_DB,
        sql={"sql": "SELECT name FROM pokemon WHERE speed > 130", "reason": "ok"},
    )
    body = make_client(provider).post("/ask", json={"question": "Eau rapides ?"}).json()

    assert body["sources"] == [executee]
    assert [schema for _messages, schema in provider.calls] == [RoutePlan, SqlQuery, AnswerPayload]
    # Les lignes doivent parvenir au modèle : sans elles il répondrait de mémoire.
    assert "barraskewda" in provider.calls[-1][0][1]["content"]


def test_un_sql_refuse_replie_sur_la_memoire(make_client, monkeypatch):
    """Un refus de sécurité ne doit pas devenir une erreur HTTP : la question
    reçoit une réponse, sans source, et le gain de l'outil reste mesurable."""

    def refuse(sql: str):
        raise SqlRefused("table interdite")

    monkeypatch.setattr("src.agent.graph.run_query", refuse)
    provider = FakeProvider(
        [ANSWER], route=BESOIN_DB, sql={"sql": "SELECT * FROM secrets", "reason": "ok"}
    )
    resp = make_client(provider).post("/ask", json={"question": "Poids ?"})

    assert resp.status_code == 200
    assert resp.json()["sources"] == []


def test_zero_ligne_est_annonce_au_modele(make_client, monkeypatch):
    """Un résultat vide doit être dit, pas comblé de mémoire."""
    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([], "SELECT 1 LIMIT 200"))
    provider = FakeProvider([ANSWER], route=BESOIN_DB, sql={"sql": "SELECT 1", "reason": "ok"})
    make_client(provider).post("/ask", json={"question": "Poids ?"})

    contenu = provider.calls[-1][0][1]["content"]
    assert "(0)" in contenu


def test_la_consommation_cumule_tous_les_appels(make_client, monkeypatch):
    """Le pipeline coûte deux ou trois appels selon le chemin : publier le coût
    d'un seul serait faux, et le figer à deux le deviendrait au jalon 4."""
    provider = FakeProvider([ANSWER])
    body = make_client(provider).post("/ask", json={"question": "Poids ?"}).json()
    assert body["usage"]["input_tokens"] == 240  # 120 × 2
    assert body["usage"]["cost_usd"] == pytest.approx(0.00014)

    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([], "SELECT 1 LIMIT 200"))
    outille = FakeProvider([ANSWER], route=BESOIN_DB, sql={"sql": "SELECT 1", "reason": "ok"})
    body = make_client(outille).post("/ask", json={"question": "Poids ?"}).json()
    assert body["usage"]["input_tokens"] == 360  # 120 × 3
