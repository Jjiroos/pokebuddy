"""Accès à PostgreSQL. Le moteur est créé paresseusement : importer ce module
ne doit pas ouvrir de connexion (les tests unitaires n'ont pas de base)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@lru_cache
def get_ro_engine() -> Engine:
    """Moteur du rôle en lecture seule. **Réservé à l'outil SQL.**

    Rien d'autre ne doit l'utiliser, et l'outil SQL ne doit jamais utiliser
    l'autre : c'est ce cloisonnement qui fait que le SQL écrit par un modèle ne
    peut rien détruire, même quand le reste des défenses a échoué.
    """
    return create_engine(get_settings().database_url_ro, pool_pre_ping=True, future=True)


def ping() -> bool:
    """Vrai si la base répond. Utilisé par /health, ne lève jamais."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
