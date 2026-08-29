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


class RoutePlan(_Strict):
    """Où aller chercher de quoi répondre. Décidé avant toute exécution.

    Les deux sources sont indépendantes : une question peut demander la base, le
    corpus, **les deux**, ou aucune. Tout mettre à faux est une réponse légitime
    — elle dit « aucun outil ne répond à ça » — et forcer le routeur à choisir
    quelque chose fabriquerait des recherches plausibles et vides.

    `needs_db` n'est qu'un booléen, et pas la requête elle-même : sur une
    question multi-outils, **le SQL ne peut pas être écrit à ce stade**. « Quel
    Pokémon mange 400 kg par jour, et quel est son numéro national ? » exige de
    savoir d'abord qu'il s'agit de Ronflex. La requête s'écrit après le corpus,
    dans un second appel qui voit ce qu'il a trouvé.

    `lore_query` n'est pas la question : c'est elle **reformulée en
    affirmation**, à la manière d'une entrée de Pokédex. Le modèle de plongement
    est symétrique — entraîné sur des paires de phrases de même nature — et
    apparier une question à une affirmation le met en échec. Mesuré sur huit
    cas : 1 bonne réponse en tête avec la question brute, 8 avec l'affirmation.

    `species` est l'espèce que la question **nomme**, telle qu'elle l'écrit, et
    rien d'autre. C'est le champ qui sépare les deux régimes mesurés au jalon 4.
    Quand la question nomme l'espèce et cherche le fait (« que mange Ronflex,
    selon le Pokédex ? »), la bonne opération est un **filtre** : la similarité
    n'a rien à trancher, et lui demander une phrase affirmative oblige à
    inventer la réponse — c'est ce qui coûtait la moitié de la suite lore.
    Quand la question donne le fait et cherche l'espèce (« quel Pokémon mange
    400 kg par jour ? »), `species` vaut null et la similarité redevient le bon
    outil, celui qui rend 90 %.
    """

    needs_db: bool
    lore_query: str | None
    species: str | None
    reason: str


class SqlQuery(_Strict):
    """La requête, écrite une fois le corpus consulté."""

    sql: str | None
    reason: str


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
