"""Point d'entrée FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import openai
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from src.api.routes import router
from src.llm.factory import get_provider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Construire le fournisseur au démarrage fait échouer le service tout de
    # suite si OPENAI_API_KEY manque, plutôt qu'à la première requête d'un
    # utilisateur.
    app.state.llm = get_provider()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Pokébuddy",
        version="0.1.0",
        summary="Agent Pokédex — jalon 1 : ligne de base, LLM nu sans outil.",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.exception_handler(openai.AuthenticationError)
    async def _auth_error(_: Request, exc: openai.AuthenticationError) -> JSONResponse:
        """Une clé absente fait échouer le démarrage, mais une clé *invalide* ne
        se découvre qu'au premier appel. Autant le dire clairement plutôt que de
        laisser un 500 opaque."""
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Le fournisseur LLM a rejeté la clé d'API. "
                "Vérifier OPENAI_API_KEY dans .env."
            },
        )

    @app.exception_handler(openai.APIError)
    async def _api_error(_: Request, exc: openai.APIError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": f"Le fournisseur LLM est indisponible : {exc.__class__.__name__}"},
        )

    return app


app = create_app()
