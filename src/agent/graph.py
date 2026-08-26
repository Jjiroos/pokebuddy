"""Le routeur, en graphe LangGraph.

**Ce que LangGraph apporte ici, et ce qu'il n'apporte pas.** Le graphe est une
chaîne à sauts conditionnels, pas un éventail parallèle : la requête SQL ne peut
pas s'écrire avant que le corpus ait dit de quel Pokémon on parle. Le
parallélisme qu'offre LangGraph n'est donc pas utilisé. Ce qu'il apporte
réellement, ce sont des étapes nommées et un état typé — qui se transposent un
pour un en spans de trace, et sur lesquels le jalon 5 pourra brancher un outil
de plus sans rien réécrire.

**`src/llm/` n'est pas touché.** Les nœuds appellent le `Protocol` existant :
aucune abstraction LangChain ne remonte dans la couche fournisseur, ce qui laisse
intacts le cache disque, le grand livre des coûts et le balayage de modèles.

Aucun nœud ne lève. Un outil qui échoue laisse l'autre continuer, et deux outils
qui échouent ramènent au chemin nu du jalon 1 : la question reçoit une réponse,
sans source. Refuser de répondre hors périmètre ferait chuter les catégories que
les outils ne couvrent pas, et le tableau d'évaluation mesurerait le refus autant
que le gain.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, TypedDict

from sqlalchemy.exc import SQLAlchemyError

from src.api.prompts import (
    ANSWER_FROM_SOURCES_PROMPT,
    ROUTER_PROMPT,
    SQL_PROMPT,
    system_prompt,
)
from src.api.schemas import AnswerPayload, Persona, RoutePlan, SqlQuery
from src.llm.provider import LLMProvider, LLMResponse
from src.tools.rag import LoreHit, search
from src.tools.schema_prompt import schema_description
from src.tools.sql import SqlRefused, run_query

# Au-delà, les lignes mangent la fenêtre de contexte sans rien apprendre de plus
# au modèle : une question dont la réponse tient dans 60 lignes est déjà une
# question mal posée.
MAX_ROWS_IN_PROMPT = 60
LORE_HITS = 5

log = logging.getLogger("agent")


class SchemaViolation(RuntimeError):
    """Le modèle n'a pas rendu de sortie conforme au schéma demandé."""


class Refusal(RuntimeError):
    """Le modèle a refusé de répondre. Remonté, jamais avalé."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AgentState(TypedDict, total=False):
    llm: LLMProvider
    question: str
    persona: str
    needs_db: bool
    lore_query: str | None
    lore_hits: list[LoreHit]
    sql_rows: list[dict[str, Any]] | None
    executed_sql: str | None
    answer: AnswerPayload
    responses: list[LLMResponse]


def _parsed(resp: LLMResponse) -> Any:
    if resp.refusal:
        raise Refusal(resp.refusal)
    if resp.parsed is None:
        raise SchemaViolation("Le modèle n'a pas renvoyé de sortie conforme au schéma.")
    return resp.parsed


def _call(state: AgentState, messages: list[dict[str, str]], schema: type) -> tuple[Any, list]:
    resp = state["llm"].complete(messages, schema=schema)
    return _parsed(resp), [*state.get("responses", []), resp]


# --- nœuds ----------------------------------------------------------------


def route(state: AgentState) -> dict[str, Any]:
    """Quelles sources consulter. Un appel, et pas encore de requête."""
    plan, responses = _call(
        state,
        [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": state["question"]},
        ],
        RoutePlan,
    )
    assert isinstance(plan, RoutePlan)
    if not plan.needs_db and plan.lore_query is None:
        log.info("aucun outil pour « %s » : %s", state["question"], plan.reason)
    return {
        "needs_db": plan.needs_db,
        "lore_query": plan.lore_query,
        "responses": responses,
    }


def search_lore(state: AgentState) -> dict[str, Any]:
    """Le corpus. Un échec n'interrompt rien : l'autre source peut suffire."""
    try:
        hits = search(state["lore_query"] or "", k=LORE_HITS)
    except SQLAlchemyError as exc:
        log.warning("recherche dans le corpus en échec (%s)", type(exc).__name__)
        hits = []
    if not hits:
        log.info("corpus muet sur « %s »", state.get("lore_query"))
    return {"lore_hits": hits}


def write_sql(state: AgentState) -> dict[str, Any]:
    """La requête, écrite en connaissance de ce que le corpus a trouvé.

    C'est ce décalage qui rend une question multi-outils soluble : « quel Pokémon
    mange 400 kg par jour, et quel est son numéro national ? » n'est traduisible
    en SQL qu'une fois qu'on sait qu'il s'agit de Ronflex.
    """
    contexte = _lore_block(state.get("lore_hits") or [])
    question = state["question"] if not contexte else f"{state['question']}\n\n{contexte}"
    query, responses = _call(
        state,
        [
            {"role": "system", "content": SQL_PROMPT.format(schema=schema_description())},
            {"role": "user", "content": question},
        ],
        SqlQuery,
    )
    assert isinstance(query, SqlQuery)

    rows: list[dict[str, Any]] | None = None
    executed: str | None = None
    if query.sql:
        try:
            rows, executed = run_query(query.sql)
        except SqlRefused as exc:
            log.warning("SQL refusé (%s) : %s", exc, query.sql)
        except SQLAlchemyError as exc:
            log.warning("SQL en échec (%s) : %s", type(exc).__name__, query.sql)
    return {"sql_rows": rows, "executed_sql": executed, "responses": responses}


def write_answer(state: AgentState) -> dict[str, Any]:
    """Sans source récoltée, c'est exactement le chemin nu du jalon 1."""
    rows = state.get("sql_rows")
    hits = state.get("lore_hits") or []
    persona = Persona(state["persona"])

    if rows is None and not hits:
        messages = [
            {"role": "system", "content": system_prompt(persona)},
            {"role": "user", "content": state["question"]},
        ]
    else:
        messages = [
            {
                "role": "system",
                "content": f"{system_prompt(persona)}\n\n{ANSWER_FROM_SOURCES_PROMPT}",
            },
            {"role": "user", "content": _sources_message(state["question"], rows, hits)},
        ]
    answer, responses = _call(state, messages, AnswerPayload)
    return {"answer": answer, "responses": responses}


# --- mise en forme des sources -------------------------------------------


def _lore_block(hits: list[LoreHit]) -> str:
    if not hits:
        return ""
    lignes = "\n".join(
        f"- {h.species_fr} (identifiant `{h.species_en}`, jeu {h.version}) : {h.text}" for h in hits
    )
    return f"Extraits du Pokédex qui pourraient identifier le sujet :\n{lignes}"


def _sources_message(question: str, rows: list[dict[str, Any]] | None, hits: list[LoreHit]) -> str:
    parties = [f"Question : {question}"]
    if hits:
        parties.append(_lore_block(hits))
    if rows is not None:
        montrees = rows[:MAX_ROWS_IN_PROMPT]
        corps = json.dumps(montrees, ensure_ascii=False, default=str)
        tronque = (
            ""
            if len(montrees) == len(rows)
            else f"\n({len(montrees)} lignes montrées sur {len(rows)}.)"
        )
        parties.append(f"Lignes renvoyées par la base ({len(rows)}) :\n{corps}{tronque}")
    return "\n\n".join(parties)


def sources_of(state: AgentState) -> list[str]:
    """Les citations, dans l'ordre où elles ont été consultées.

    Le SQL **réellement exécuté**, `LIMIT` compris — citer la requête proposée
    plutôt que celle exécutée serait une citation fausse.
    """
    citations = [h.citation for h in state.get("lore_hits") or []]
    if state.get("executed_sql"):
        citations.append(str(state["executed_sql"]))
    return citations


# --- le graphe ------------------------------------------------------------


def _after_route(state: AgentState) -> str:
    if state.get("lore_query"):
        return "search_lore"
    return "write_sql" if state.get("needs_db") else "write_answer"


def _after_lore(state: AgentState) -> str:
    return "write_sql" if state.get("needs_db") else "write_answer"


@lru_cache
def compiled_graph():
    """Compilé une fois par processus : la topologie ne dépend pas de la requête."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(AgentState)
    builder.add_node("route", route)
    builder.add_node("search_lore", search_lore)
    builder.add_node("write_sql", write_sql)
    builder.add_node("write_answer", write_answer)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route", _after_route, ["search_lore", "write_sql", "write_answer"]
    )
    builder.add_conditional_edges("search_lore", _after_lore, ["write_sql", "write_answer"])
    builder.add_edge("write_sql", "write_answer")
    builder.add_edge("write_answer", END)
    return builder.compile()


def answer_question(llm: LLMProvider, question: str, persona: str) -> AgentState:
    state: AgentState = {
        "llm": llm,
        "question": question,
        "persona": persona,
        "responses": [],
        "lore_hits": [],
    }
    return compiled_graph().invoke(state)  # type: ignore[return-value]
