"""Traçage Langfuse, **optionnel par construction**.

Un pipeline à trois appels et deux outils est illisible dans un journal plat :
c'est là que Langfuse gagne son inscription. Pas sur le coût — `llm_calls` le
mesure déjà mieux, puisqu'il sait ce que le cache a servi.

**La promesse « le dépôt tourne sans les clés » est la nôtre, pas celle du
SDK.** Sans `LANGFUSE_PUBLIC_KEY`, `span()` rend un objet muet : rien n'est
importé, rien n'est construit, aucune socket n'est ouverte. Se reposer sur « le
SDK attrape ses erreurs » ferait dépendre une garantie du dépôt d'une note de
version d'un tiers, et un test vérifie donc la nôtre.

Le muet n'existe pas pour la beauté du motif : il évite un `if trace:` à chaque
point d'instrumentation du graphe. Un traçage qui se voit dans le code qu'il
observe finit par être retiré.

**Portée du filet.** Le `try` couvre la *construction* du client et du span,
c'est-à-dire ce que le traçage peut casser. Une exception levée par le corps du
`with` traverse — elle appartient à l'appelant, et l'avaler ferait d'un outil
d'observation une cause de panne silencieuse.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Protocol

from src.config import get_settings

log = logging.getLogger("obs")

# Les types d'observation de Langfuse utilisés ici. « retriever » et « tool »
# ne sont pas décoratifs : l'interface les distingue d'un appel de modèle, ce
# qui est exactement la lecture qu'on veut d'un pipeline outillé.
KIND_SPAN = "span"
KIND_GENERATION = "generation"
KIND_TOOL = "tool"
KIND_RETRIEVER = "retriever"


class Span(Protocol):
    def update(self, **fields: Any) -> Any: ...


class _Muet:
    """Le span quand le traçage est éteint. Absorbe tout, ne fait rien."""

    def update(self, **fields: Any) -> _Muet:
        return self


MUET = _Muet()


def enabled() -> bool:
    """Les deux clés, pas une. Une paire incomplète est une configuration
    ratée, pas une demi-activation — et le dire tôt vaut mieux qu'un 401 à la
    première trace."""
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


@lru_cache
def _client() -> Any | None:
    """Un client par processus, ou `None`. L'import du SDK est local : sans
    clés, `langfuse` n'est jamais chargé."""
    if not enabled():
        return None
    try:
        from langfuse import Langfuse

        settings = get_settings()
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:  # noqa: BLE001 - le traçage ne casse jamais l'appelant
        log.warning("traçage désactivé : %s (%s)", exc, type(exc).__name__)
        return None


@contextmanager
def span(name: str, *, kind: str = KIND_SPAN, **fields: Any) -> Iterator[Span]:
    """Une observation nommée, ou le muet si le traçage est éteint."""
    client = _client()
    contexte = None
    if client is not None:
        try:
            contexte = client.start_as_current_observation(name=name, as_type=kind, **fields)
        except Exception as exc:  # noqa: BLE001
            log.warning("span « %s » non ouvert : %s", name, type(exc).__name__)
    if contexte is None:
        yield MUET
        return
    with contexte as observation:
        yield observation


@contextmanager
def run_context(label: str | None) -> Iterator[None]:
    """Étiquette toutes les traces d'un run d'évaluation.

    C'est ce qui rend deux runs comparables dans l'interface, comme
    `run_label` les rend comparables dans `llm_calls`.
    """
    client = _client()
    if client is None or not label:
        yield
        return
    from langfuse import propagate_attributes

    with propagate_attributes(tags=[label]):
        yield


def flush() -> None:
    """À appeler avant de quitter un script.

    Le SDK exporte par lots en tâche de fond : un processus court se termine
    avant l'envoi, et le run n'aurait laissé aucune trace.
    """
    client = _client()
    if client is not None:
        client.flush()
