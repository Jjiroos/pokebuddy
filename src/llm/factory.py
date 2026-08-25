"""Sélection du fournisseur. Changer de LLM = changer une variable d'environnement."""

from __future__ import annotations

from functools import lru_cache

from src.config import get_settings
from src.llm.cache import LLMCache
from src.llm.openai_provider import OpenAIProvider
from src.llm.provider import LLMProvider


@lru_cache
def get_cache() -> LLMCache:
    return LLMCache(get_settings().llm_cache_path)


@lru_cache
def get_provider(model: str | None = None) -> LLMProvider:
    """`model` permet à l'éval de balayer plusieurs modèles sans que rien
    en dehors de ce module n'ait à connaître le fournisseur."""
    settings = get_settings()
    match settings.llm_provider:
        case "openai":
            return OpenAIProvider(
                model=model or settings.openai_model,
                cache=get_cache(),
                base_url=settings.openai_base_url,
                reasoning_effort=settings.openai_reasoning_effort,
                verbosity=settings.openai_verbosity,
                timeout_s=settings.openai_timeout_s,
                max_retries=settings.openai_max_retries,
            )
        case unknown:
            # Mistral et Ollama arrivent au jalon 5, pour le tableau comparatif.
            raise ValueError(f"LLM_PROVIDER inconnu : « {unknown} ». Valeurs supportées : openai.")
