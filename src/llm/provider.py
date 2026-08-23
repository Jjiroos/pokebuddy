"""Contrat commun à tous les fournisseurs de LLM.

Changer de fournisseur doit rester un changement de variable d'environnement
(LLM_PROVIDER) : rien en dehors de src/llm/ ne connaît OpenAI.

Le contrat est volontairement **synchrone**. Il n'y a pas de fan-out d'entrées/
sorties à l'intérieur d'une requête à ce jalon ; FastAPI exécute déjà les routes
synchrones dans un pool de threads, et le cache SQLite est synchrone. L'éval du
jalon 2 parallélisera par threads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from pydantic import BaseModel


class Message(TypedDict):
    role: str
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """Réponse d'un LLM, coût et consommation compris.

    Les tokens et le coût voyagent dans le type de retour plutôt que dans un log :
    c'est ce qui rend la colonne « coût / requête » du tableau d'évaluation
    triviale à remplir, et impossible à oublier.
    """

    text: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    cache_hit: bool
    parsed: Any | None = None
    refusal: str | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None = None,
        run_label: str | None = None,
    ) -> LLMResponse:
        """Un aller-retour LLM. Si `schema` est fourni, la sortie est validée contre lui."""
        ...
