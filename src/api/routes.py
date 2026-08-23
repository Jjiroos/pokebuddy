"""Les trois routes du jalon 1 : /health, /ask, /extract.

Les handlers sont synchrones : FastAPI les exécute dans un pool de threads, ce
qui s'accorde avec le contrat synchrone du fournisseur et le cache SQLite.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_llm
from src.api.prompts import EXTRACT_PROMPT, system_prompt
from src.api.schemas import (
    AnswerPayload,
    AskRequest,
    AskResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    PokemonFacts,
    Usage,
)
from src.db.session import ping
from src.llm.provider import LLMProvider, LLMResponse

router = APIRouter()

LLM = Annotated[LLMProvider, Depends(get_llm)]


def _usage(resp: LLMResponse) -> Usage:
    return Usage(
        model=resp.model,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cost_usd=resp.cost_usd,
        latency_ms=resp.latency_ms,
        cache_hit=resp.cache_hit,
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


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, llm: LLM) -> AskResponse:
    """Appel LLM nu — ni base, ni recherche, ni outil.

    C'est la ligne de base du projet. Ses erreurs sont le résultat attendu :
    `sources` repart vide parce qu'il n'y a rien à citer.
    """
    resp = llm.complete(
        [
            {"role": "system", "content": system_prompt(req.persona)},
            {"role": "user", "content": req.question},
        ],
        schema=AnswerPayload,
    )
    _reject_refusal(resp)
    if resp.parsed is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Le modèle n'a pas renvoyé de sortie conforme au schéma.",
        )

    payload: AnswerPayload = resp.parsed
    return AskResponse(
        answer=payload.answer,
        confidence=payload.confidence,
        sources=[],
        usage=_usage(resp),
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
