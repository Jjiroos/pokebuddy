"""Le graphe du jalon 4, et surtout ses cas dégradés.

**C'est le vrai contenu du jalon, pas la topologie.** Un graphe qui enchaîne
trois nœuds quand tout va bien n'apporte rien ; ce qui se mesure, c'est ce
qu'il fait quand une source se tait, refuse ou tombe. Chaque ligne du tableau
du plan a son test ici, et aucune ne remonte en erreur HTTP.

Aucun test ne sort sur le réseau ni ne touche Postgres : `search` et
`run_query` sont doublés, et le fournisseur est le double de `conftest`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from src.agent.graph import Refusal, SchemaViolation, answer_question, sources_of
from src.api.schemas import AnswerPayload, RoutePlan, SqlQuery
from src.tools.rag import LoreHit
from src.tools.sql import SqlRefused
from tests.conftest import FakeProvider

ANSWER = {"answer": "Téraclope est de type Spectre.", "confidence": 0.9}

AUCUN_OUTIL = {"needs_db": False, "lore_query": None, "reason": "de mémoire"}
BESOIN_DB = {"needs_db": True, "lore_query": None, "reason": "fait chiffré"}
BESOIN_CORPUS = {"needs_db": False, "lore_query": "Il avale des feux follets.", "reason": "lore"}
BESOIN_DES_DEUX = {
    "needs_db": True,
    "lore_query": "Il avale des feux follets.",
    "reason": "corpus puis base",
}

TERACLOPE = LoreHit(
    species_fr="Téraclope",
    species_en="dusclops",
    version="black",
    text="Il cherche à avaler des feux follets.",
    distance=0.12,
)


def _ask(provider: FakeProvider, question: str = "Question ?"):
    return answer_question(provider, question, "pokedex")


def _invite_de_redaction(provider: FakeProvider) -> str:
    """Le dernier appel est toujours celui qui rédige."""
    messages, schema = provider.calls[-1]
    assert schema is AnswerPayload
    return "\n\n".join(m["content"] for m in messages)


# --- topologie : le routeur décide quels nœuds tournent -------------------


@pytest.mark.parametrize(
    ("route", "attendus"),
    [
        (AUCUN_OUTIL, [RoutePlan, AnswerPayload]),
        (BESOIN_DB, [RoutePlan, SqlQuery, AnswerPayload]),
        (BESOIN_CORPUS, [RoutePlan, AnswerPayload]),
        (BESOIN_DES_DEUX, [RoutePlan, SqlQuery, AnswerPayload]),
    ],
)
def test_le_routeur_commande_les_appels(monkeypatch, route, attendus):
    """Le corpus ne coûte aucun appel LLM ; la base en coûte un de plus."""
    monkeypatch.setattr("src.agent.graph.search", lambda q, k=5: [TERACLOPE])
    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([], "SELECT 1 LIMIT 200"))
    provider = FakeProvider([ANSWER], route=route, sql={"sql": "SELECT 1", "reason": "ok"})
    _ask(provider)
    assert [schema for _messages, schema in provider.calls] == attendus


def test_le_corpus_est_consulte_avant_que_le_sql_s_ecrive(monkeypatch):
    """La raison d'être du jalon : sur une question multi-outils, la requête
    n'est écrite qu'une fois l'espèce identifiée. L'ordre n'est pas cosmétique
    — sans lui, « quel Pokémon avale des feux follets, et de quels types
    est-il ? » n'a pas de filtre à écrire."""
    monkeypatch.setattr("src.agent.graph.search", lambda q, k=5: [TERACLOPE])
    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([{"t": "ghost"}], "SELECT 1"))
    provider = FakeProvider([ANSWER], route=BESOIN_DES_DEUX, sql={"sql": "SELECT 1", "reason": ""})
    _ask(provider)

    invite_sql = "\n\n".join(m["content"] for m in provider.calls[1][0])
    assert provider.calls[1][1] is SqlQuery
    assert "dusclops" in invite_sql


# --- les cinq cas dégradés du plan ----------------------------------------


def test_aucun_outil_choisi_repond_de_memoire_sans_source():
    """Première ligne du tableau : le routeur a le droit de ne rien choisir."""
    provider = FakeProvider([ANSWER], route=AUCUN_OUTIL)
    etat = _ask(provider)
    assert etat["answer"].answer == ANSWER["answer"]
    assert sources_of(etat) == []
    # Le chemin nu du jalon 1 : on ne demande pas au modèle de se fonder sur
    # des éléments qu'on ne lui a pas donnés.
    assert "UNIQUEMENT sur les éléments fournis" not in _invite_de_redaction(provider)


def test_question_hors_perimetre_ne_devient_pas_un_refus():
    """Cinquième ligne : hors périmètre, le routeur dit « aucun outil » et la
    question reçoit quand même une réponse. Refuser ferait chuter les
    catégories que les outils ne couvrent pas, et l'évaluation mesurerait le
    refus autant que le gain."""
    provider = FakeProvider([ANSWER], route=AUCUN_OUTIL)
    etat = _ask(provider, "Quelle est la capitale de la France ?")
    assert etat["answer"].answer
    assert sources_of(etat) == []


def test_un_sql_refuse_laisse_le_corpus_continuer(monkeypatch):
    """Deuxième ligne : un outil qui échoue n'emporte pas l'autre."""

    def refuse(sql: str):
        raise SqlRefused("table interdite")

    monkeypatch.setattr("src.agent.graph.search", lambda q, k=5: [TERACLOPE])
    monkeypatch.setattr("src.agent.graph.run_query", refuse)
    provider = FakeProvider(
        [ANSWER], route=BESOIN_DES_DEUX, sql={"sql": "SELECT * FROM secrets", "reason": ""}
    )
    etat = _ask(provider)

    assert sources_of(etat) == ["pokedex:dusclops/black"]
    assert "feux follets" in _invite_de_redaction(provider)


def test_un_corpus_en_panne_laisse_le_sql_continuer(monkeypatch):
    """Le symétrique : la base peut suffire quand la recherche vectorielle
    tombe. Une panne de pgvector ne doit pas faire un 500."""

    def tombe(query, k=5):
        raise OperationalError("SELECT 1", {}, Exception("pgvector absent"))

    monkeypatch.setattr("src.agent.graph.search", tombe)
    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([{"n": 143}], "SELECT n LIMIT 1"))
    provider = FakeProvider([ANSWER], route=BESOIN_DES_DEUX, sql={"sql": "SELECT n", "reason": ""})
    etat = _ask(provider)

    assert sources_of(etat) == ["SELECT n LIMIT 1"]
    assert "143" in _invite_de_redaction(provider)


def test_les_deux_outils_en_echec_replient_sur_la_memoire(monkeypatch):
    """Troisième ligne : le repli est celui du jalon 1, réponse comprise."""

    def refuse(sql: str):
        raise SqlRefused("table interdite")

    monkeypatch.setattr("src.agent.graph.search", lambda q, k=5: [])
    monkeypatch.setattr("src.agent.graph.run_query", refuse)
    provider = FakeProvider([ANSWER], route=BESOIN_DES_DEUX, sql={"sql": "SELECT 1", "reason": ""})
    etat = _ask(provider)

    assert etat["answer"].answer == ANSWER["answer"]
    assert sources_of(etat) == []
    assert "UNIQUEMENT sur les éléments fournis" not in _invite_de_redaction(provider)


def test_zero_ligne_et_zero_passage_sont_annonces_au_modele(monkeypatch):
    """Quatrième ligne, et la nuance qui compte : « refusé » et « zéro
    résultat » ne sont pas le même cas. Une requête qui s'exécute et ne rend
    rien est une information — la source ne contient pas la réponse — et la
    taire ferait répondre de mémoire à une question à laquelle la base a
    répondu « non »."""
    monkeypatch.setattr("src.agent.graph.search", lambda q, k=5: [])
    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([], "SELECT 1 LIMIT 200"))
    provider = FakeProvider([ANSWER], route=BESOIN_DES_DEUX, sql={"sql": "SELECT 1", "reason": ""})
    _ask(provider)

    invite = _invite_de_redaction(provider)
    assert "UNIQUEMENT sur les éléments fournis" in invite
    assert "(0)" in invite


# --- citations ------------------------------------------------------------


def test_les_sources_citent_la_requete_executee_pas_celle_proposee(monkeypatch):
    """Citer la requête proposée plutôt que celle exécutée — LIMIT réécrit
    compris — serait une citation fausse : elle ne rejouerait pas le résultat."""
    monkeypatch.setattr(
        "src.agent.graph.run_query", lambda sql: ([{"name": "snorlax"}], f"{sql} LIMIT 200")
    )
    provider = FakeProvider([ANSWER], route=BESOIN_DB, sql={"sql": "SELECT name", "reason": ""})
    etat = _ask(provider)
    assert sources_of(etat) == ["SELECT name LIMIT 200"]


def test_une_question_multi_outils_cite_deux_sources_de_nature_differente(monkeypatch):
    """La promesse du jalon, sous sa forme vérifiable."""
    monkeypatch.setattr("src.agent.graph.search", lambda q, k=5: [TERACLOPE])
    monkeypatch.setattr("src.agent.graph.run_query", lambda sql: ([{"t": "ghost"}], "SELECT t"))
    provider = FakeProvider([ANSWER], route=BESOIN_DES_DEUX, sql={"sql": "SELECT t", "reason": ""})
    etat = _ask(provider)
    assert sources_of(etat) == ["pokedex:dusclops/black", "SELECT t"]


# --- ce qui doit remonter -------------------------------------------------


def test_un_refus_du_modele_remonte(monkeypatch):
    """Un refus arrive comme un contenu, pas comme une exception : l'avaler
    produirait une réponse vide et silencieuse."""
    with pytest.raises(Refusal):
        _ask(FakeProvider(refusal="contenu non conforme"))


def test_une_sortie_non_conforme_remonte():
    provider = FakeProvider([], route=AUCUN_OUTIL)
    with pytest.raises(SchemaViolation):
        _ask(provider)
