"""Schémas d'entrée et de sortie de l'API.

Note sur les sorties structurées OpenAI : en mode strict, tout champ doit être
requis et aucun objet ne peut accepter de propriété supplémentaire. Un champ
« optionnel » se modélise donc en ``X | None`` **sans valeur par défaut**, et
non en champ à défaut. Les modèles envoyés au LLM (BaseStats, PokemonFacts,
AnswerPayload) suivent cette règle ; les modèles purement HTTP sont libres.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Persona(StrEnum):
    """Le §9 du plan veut mesurer l'effet de la persona sur l'exactitude.

    Le commutateur est câblé dès le jalon 1 : le rétrofiter après la ligne de
    base obligerait à rejouer tous les runs d'évaluation.
    """

    pokedex = "pokedex"
    factual = "factual"


# --- modèles soumis au LLM (mode strict) ---------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerPayload(_Strict):
    answer: str
    confidence: float


class BaseStats(_Strict):
    hp: int | None
    attack: int | None
    defense: int | None
    special_attack: int | None
    special_defense: int | None
    speed: int | None


class PokemonFacts(_Strict):
    name: str | None
    national_dex_number: int | None
    types: list[str]
    base_stats: BaseStats | None
    evolves_from: str | None
    confidence: float


# --- modèles HTTP ---------------------------------------------------------


class Usage(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    cache_hit: bool


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    persona: Persona = Persona.pokedex


class AskResponse(BaseModel):
    answer: str
    confidence: float
    # Reste vide au jalon 1 : l'appel est nu, il n'y a rien à citer. Le champ
    # existe pour que le gain du jalon 3 se lise dans un schéma inchangé.
    sources: list[str] = Field(default_factory=list)
    usage: Usage


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ExtractResponse(BaseModel):
    facts: PokemonFacts
    usage: Usage


class HealthResponse(BaseModel):
    status: str
    db: str
    llm_provider: str
    model: str
