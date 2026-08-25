"""Les trois routes : /health, /ask, /extract.

Les handlers sont synchrones : FastAPI les exécute dans un pool de threads, ce
qui s'accorde avec le contrat synchrone du fournisseur et le cache SQLite.

Depuis le jalon 3, `/ask` est un pipeline en deux temps — traduire la question
en SQL, puis rédiger à partir des lignes. Pas de framework d'agent : il arrive au
jalon 4, et l'empiler ici rendrait illisible ce que gagne le SQL seul.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from src.api.deps import get_llm
from src.api.prompts import (
    ANSWER_FROM_ROWS_PROMPT,
    EXTRACT_PROMPT,
    SQL_PROMPT,
    system_prompt,
)
from src.api.schemas import (
    AnswerPayload,
    AskRequest,
    AskResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    PokemonFacts,
    SqlPlan,
    Usage,
)
from src.db.session import ping
from src.llm.provider import LLMProvider, LLMResponse
from src.tools.schema_prompt import schema_description
from src.tools.sql import SqlRefused, run_query

router = APIRouter()

LLM = Annotated[LLMProvider, Depends(get_llm)]

log = logging.getLogger("api.ask")

# Au-delà, les lignes mangent la fenêtre de contexte sans rien apprendre de plus
# au modèle : une question dont la réponse tient dans 60 lignes est déjà une
# question mal posée.
MAX_ROWS_IN_PROMPT = 60


def _usage(*responses: LLMResponse) -> Usage:
    """Consommation cumulée du pipeline.

    Deux appels par question depuis le jalon 3 : les additionner est ce qui rend
    honnête la colonne « coût par question » du tableau d'évaluation. Le run est
    marqué servi par le cache seulement si **tous** les appels l'ont été, sans
    quoi la latence d'un run rejoué serait fausse.
    """
    return Usage(
        model=responses[-1].model,
        input_tokens=sum(r.input_tokens for r in responses),
        output_tokens=sum(r.output_tokens for r in responses),
        cost_usd=sum(r.cost_usd for r in responses),
        latency_ms=sum(r.latency_ms for r in responses),
        cache_hit=all(r.cache_hit for r in responses),
    )


def _reject_refusal(resp: LLMResponse) -> None:
    if resp.refusal:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Le modèle a refusé de répondre : {resp.refusal}",
        )


@router.get("/health", response_model=HealthResponse)
def health(llm: LLM) -> HealthResponse:
    db_ok = ping()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db="ok" if db_ok else "unreachable",
        llm_provider=llm.name,
        model=llm.model,
    )


def _parsed(resp: LLMResponse) -> object:
    _reject_refusal(resp)
    if resp.parsed is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Le modèle n'a pas renvoyé de sortie conforme au schéma.",
        )
    return resp.parsed


def _plan_sql(llm: LLMProvider, question: str) -> tuple[SqlPlan, LLMResponse]:
    """Premier temps : traduire la question en une requête, ou renoncer."""
    resp = llm.complete(
        [
            {"role": "system", "content": SQL_PROMPT.format(schema=schema_description())},
            {"role": "user", "content": question},
        ],
        schema=SqlPlan,
    )
    plan: SqlPlan = _parsed(resp)  # type: ignore[assignment]
    return plan, resp


def _consult_db(sql: str) -> tuple[list[dict], str] | None:
    """Exécute la requête, ou renvoie `None` si elle est refusée ou échoue.

    Aucun de ces cas n'est une erreur HTTP : le SQL généré est une tentative, et
    une tentative ratée doit replier sur la réponse de mémoire plutôt que de
    renvoyer un 500 à l'utilisateur. La cause est journalisée — c'est elle qui
    dira, après un run, si le générateur échoue sur la sécurité ou sur le sens.
    """
    try:
        return run_query(sql)
    except SqlRefused as exc:
        log.warning("SQL refusé (%s) : %s", exc, sql)
    except SQLAlchemyError as exc:
        log.warning("SQL en échec (%s) : %s", type(exc).__name__, sql)
    return None


def _rows_message(question: str, rows: list[dict]) -> str:
    shown = rows[:MAX_ROWS_IN_PROMPT]
    body = json.dumps(shown, ensure_ascii=False, default=str)
    tronque = (
        "" if len(shown) == len(rows) else f"\n({len(shown)} lignes montrées sur {len(rows)}.)"
    )
    return f"Question : {question}\n\nLignes renvoyées par la base ({len(rows)}) :\n{body}{tronque}"


def _write_answer(
    llm: LLMProvider, req: AskRequest, rows: list[dict] | None
) -> tuple[AnswerPayload, LLMResponse]:
    """Second temps. Sans lignes, c'est exactement le chemin nu du jalon 1."""
    if rows is None:
        messages = [
            {"role": "system", "content": system_prompt(req.persona)},
            {"role": "user", "content": req.question},
        ]
    else:
        messages = [
            {
                "role": "system",
                "content": f"{system_prompt(req.persona)}\n\n{ANSWER_FROM_ROWS_PROMPT}",
            },
            {"role": "user", "content": _rows_message(req.question, rows)},
        ]
    resp = llm.complete(messages, schema=AnswerPayload)
    payload: AnswerPayload = _parsed(resp)  # type: ignore[assignment]
    return payload, resp


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, llm: LLM) -> AskResponse:
    """Deux appels : traduire la question en SQL, puis rédiger depuis les lignes.

    Le repli sur la réponse de mémoire n'est pas une facilité : il isole ce que
    l'outil apporte. Refuser de répondre hors de son périmètre ferait chuter les
    catégories que le SQL ne couvre pas, et le tableau d'évaluation mesurerait
    alors le refus autant que le gain.

    `sources` porte la requête **réellement exécutée**, `LIMIT` compris : c'est
    la citation obligatoire du plan, version base de données.
    """
    plan, plan_resp = _plan_sql(llm, req.question)
    consulted = _consult_db(plan.sql) if plan.sql else None
    if plan.sql is None:
        log.info("pas de SQL pour « %s » : %s", req.question, plan.reason)

    rows, sources = (None, []) if consulted is None else (consulted[0], [consulted[1]])
    payload, answer_resp = _write_answer(llm, req, rows)

    return AskResponse(
        answer=payload.answer,
        confidence=payload.confidence,
        sources=sources,
        usage=_usage(plan_resp, answer_resp),
    )


@router.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest, llm: LLM) -> ExtractResponse:
    """Texte libre en entrée, objet validé en sortie.

    Démontre la sortie structurée. Réutilisé au jalon 4 comme extracteur
    d'entités du routeur de l'agent.
    """
    resp = llm.complete(
        [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": req.text},
        ],
        schema=PokemonFacts,
    )
    _reject_refusal(resp)
    if resp.parsed is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Le modèle n'a pas renvoyé de sortie conforme au schéma.",
        )
    return ExtractResponse(facts=resp.parsed, usage=_usage(resp))
