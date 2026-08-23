"""Injection de dépendances FastAPI.

Le fournisseur passe par l'état de l'application plutôt que par un import
direct : les tests peuvent ainsi le remplacer par un double sans réseau.
"""

from __future__ import annotations

from fastapi import Request

from src.llm.provider import LLMProvider


def get_llm(request: Request) -> LLMProvider:
    return request.app.state.llm
