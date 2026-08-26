"""Les trois routes : /health, /ask, /extract.

Les handlers sont synchrones : FastAPI les exécute dans un pool de threads, ce
qui s'accorde avec le contrat synchrone du fournisseur et le cache SQLite.

Depuis le jalon 4, `/ask` n'orchestre plus rien lui-même : il délègue au graphe
de `src/agent/graph.py` et se contente de traduire son état final en réponse
HTTP. **Le contrat ne change pas** — `answer`, `confidence`, `sources`,
`usage` — c'est ce qui rend les runs des jalons 2, 3 et 4 comparables sans
toucher au harnais d'évaluation.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.agent.graph import Refusal, SchemaViolation, answer_question, sources_of
from src.api.deps import get_llm
from src.api.prompts import EXTRACT_PROMPT
from src.api.schemas import (
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

log = logging.getLogger("api.ask")


def _usage(*responses: LLMResponse) -> Usage:
    """Consommation cumulée du pipeline.

    Deux à trois appels par question depuis le jalon 4 : les additionner est ce
    qui rend honnête la colonne « coût par question » du tableau d'évaluation.
    Le run est marqué servi par le cache seulement si **tous** les appels l'ont
    été, sans quoi la latence d'un run rejoué serait fausse.
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


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, llm: LLM) -> AskResponse:
    """Un appel au graphe : router, consulter, rédiger.

    Aucun échec d'outil ne remonte ici : le graphe replie sur la réponse de
    mémoire, et `sources` reste vide. Ce n'est pas une facilité, c'est ce qui
    isole ce que les outils apportent — refuser de répondre hors de leur
    périmètre ferait chuter les catégories qu'ils ne couvrent pas, et le tableau
    d'évaluation mesurerait alors le refus autant que le gain.

    Seules deux causes deviennent des erreurs HTTP, et aucune n'est un échec
    d'outil : un refus du modèle (422) et une sortie non conforme au schéma
    demandé (502). Les avaler produirait une réponse vide et silencieuse.
    """
    try:
        state = answer_question(llm, req.question, req.persona)
    except Refusal as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Le modèle a refusé de répondre : {exc.reason}",
        ) from exc
    except SchemaViolation as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    payload = state["answer"]
    return AskResponse(
        answer=payload.answer,
        confidence=payload.confidence,
        sources=sources_of(state),
        usage=_usage(*state["responses"]),
    )


@router.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest, llm: LLM) -> ExtractResponse:
    """Texte libre en entrée, objet validé en sortie.

    Démontre la sortie structurée, et reste hors du graphe : un extracteur n'a
    aucune source à consulter.
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
