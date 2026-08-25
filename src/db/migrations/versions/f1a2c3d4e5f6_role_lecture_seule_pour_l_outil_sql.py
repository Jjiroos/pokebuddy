"""role lecture seule pour l'outil sql

Le jalon 3 laisse un modèle écrire du SQL. Toutes les protections applicatives
— parsing de l'AST, liste blanche de tables, LIMIT forcé — sont des filtres, et
un filtre finit par se contourner. Ce rôle est la seule couche qui tienne quand
les autres ont échoué : même un `DROP TABLE` parfaitement formé échoue faute de
droits.

Écrit en migration plutôt que dans `docker/init-db.sql` : ce dernier ne
s'exécute que sur un volume vierge, et une base déjà installée resterait sans
rôle. Tout est idempotent, la migration peut être rejouée.

Revision ID: f1a2c3d4e5f6
Revises: 9a59163039b1
Create Date: 2026-08-25 19:10:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import make_url

from src.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "f1a2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9a59163039b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ident(name: str) -> str:
    """Identifiant SQL correctement échappé."""
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    """Littéral SQL correctement échappé."""
    return "'" + value.replace("'", "''") + "'"


def _ro_credentials() -> tuple[str, str]:
    """Rôle et mot de passe, lus depuis DATABASE_URL_RO.

    Une seule source de vérité : dupliquer le mot de passe entre la migration
    et la configuration garantirait qu'ils divergent un jour.
    """
    url = make_url(get_settings().database_url_ro)
    if not url.username or not url.password:
        raise ValueError("DATABASE_URL_RO doit porter un utilisateur et un mot de passe.")
    return url.username, url.password


def upgrade() -> None:
    role, password = _ro_credentials()
    ident, secret = _ident(role), _literal(password)

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_literal(role)}) THEN
                CREATE ROLE {ident} LOGIN PASSWORD {secret};
            ELSE
                ALTER ROLE {ident} LOGIN PASSWORD {secret};
            END IF;
        END
        $$;
        """
    )

    op.execute(f"GRANT CONNECT ON DATABASE {_ident(op.get_bind().engine.url.database)} TO {ident}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ident}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ident}")
    # Les tables créées par les migrations suivantes doivent être lisibles sans
    # qu'on ait à repasser un GRANT — sans quoi l'outil SQL cesserait de voir la
    # moitié du schéma au prochain jalon.
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ident}")
    # Explicite plutôt que reposer sur le défaut de PostgreSQL 15+ : lire ne
    # doit jamais impliquer créer.
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {ident}")


def downgrade() -> None:
    role, _ = _ro_credentials()
    ident = _ident(role)

    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {ident}")
    op.execute(f"DROP OWNED BY {ident}")
    op.execute(f"DROP ROLE IF EXISTS {ident}")
