"""Écriture idempotente, partagée par les ingestions.

Relancer une ingestion ne doit rien dupliquer et doit mettre à jour ce qui a
changé — c'est ce qui rend `make ingest` rejouable sans réfléchir, et ce qui
permet d'ajouter une colonne puis de repeupler sans repartir d'une base vide.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def upsert(session: Session, model: Any, rows: Sequence[dict[str, Any]], *, keys: list[str]) -> int:
    if not rows:
        return 0
    stmt = insert(model).values(list(rows))
    updatable = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
    stmt = (
        stmt.on_conflict_do_update(index_elements=keys, set_=updatable)
        if updatable
        else stmt.on_conflict_do_nothing(index_elements=keys)
    )
    session.execute(stmt)
    return len(rows)


def chunks(rows: list[dict[str, Any]], size: int = 500) -> Iterable[list[dict[str, Any]]]:
    """Découpe les insertions : un seul INSERT de 20 000 lignes dépasse la
    limite de paramètres liés du pilote."""
    for i in range(0, len(rows), size):
        yield rows[i : i + size]
