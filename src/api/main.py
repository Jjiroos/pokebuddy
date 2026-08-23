"""Point d'entrée FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    return app


app = create_app()
