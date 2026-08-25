"""Outil SQL contraint : exécuter du SQL écrit par un modèle, sans risque.

Quatre couches, et **elles ne se valent pas** :

1. **Le rôle PostgreSQL en lecture seule** (migration `f1a2c3d4e5f6`). C'est la
   seule qui tienne encore quand les autres ont échoué : un `DROP TABLE`
   parfaitement formé échoue faute de droits. Tout le reste de ce fichier est du
   confort par-dessus.
2. **La validation d'AST** ci-dessous — une seule instruction, une expression de
   lecture à la racine, aucun nœud d'écriture nulle part dans l'arbre, tables sur
   liste blanche.
3. **`LIMIT` forcé**, par réécriture de l'arbre et jamais par concaténation.
4. **`statement_timeout`**, contre les jointures cartésiennes.

Un projet qui n'aurait que la couche 2 aurait compris le problème à moitié :
un validateur est un filtre, et un filtre finit toujours par se contourner.

Limite assumée : seules les expressions de lecture (`SELECT`, `UNION`…) passent.
Le validateur refuse ce qu'il ne sait pas analyser entièrement, plutôt que de
laisser passer ce qu'il n'a pas compris.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlalchemy import text
from sqlglot import exp

from src.db.models import Base
from src.db.session import get_ro_engine

DIALECT = "postgres"
MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 3000

# Dérivée du métamodèle, jamais écrite à la main : une table ajoutée à un jalon
# suivant ne peut pas être oubliée, et une table supprimée ne peut pas rester
# autorisée par distraction.
ALLOWED_TABLES = frozenset(Base.metadata.tables)
# `pg_catalog` et `information_schema` sont exclus sans avoir à les nommer.
ALLOWED_SCHEMAS = frozenset({"", "public"})

# Cherchés dans l'arbre **entier**, pas seulement à la racine : une CTE
# écrivante (`WITH x AS (INSERT … RETURNING *) SELECT * FROM x`) présente un
# `Select` en surface. C'est le contournement le plus élégant, et le seul qui
# passerait un contrôle naïf du premier nœud.
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Copy,
    exp.Set,
    exp.Analyze,
    # Tout ce que sqlglot n'a pas su typer atterrit ici (`VACUUM`, `CALL`…).
    # Refuser l'inconnu est le comportement correct pour un validateur.
    exp.Command,
)

# Par préfixe plutôt que par liste : `pg_read_file`, `pg_ls_dir`, `pg_sleep`,
# `lo_import`… forment une famille ouverte qu'une énumération manquerait.
# Une requête légitime sur ce schéma n'a aucune raison d'appeler du `pg_*`.
_FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_")
_FORBIDDEN_FUNCTIONS = frozenset({"dblink", "dblink_exec", "set_config", "query_to_xml"})


class SqlRefused(ValueError):
    """Le SQL proposé est refusé avant toute exécution.

    Porté jusqu'à l'appelant plutôt qu'avalé : la cause du refus est journalisée,
    et c'est elle qui dira, après un run d'évaluation, si le générateur de SQL
    échoue sur la sécurité ou sur la sémantique.
    """


def _parse(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=DIALECT) if s is not None]
    except Exception as exc:  # noqa: BLE001 — toute erreur d'analyse est un refus
        raise SqlRefused(f"SQL inanalysable : {exc}") from exc

    if not statements:
        raise SqlRefused("SQL vide.")
    # `SELECT 1; DROP TABLE cards` se voit ici, et nulle part ailleurs.
    if len(statements) > 1:
        raise SqlRefused(f"{len(statements)} instructions ; une seule est autorisée.")
    return statements[0]


def _check_readonly(root: exp.Expression) -> None:
    if not isinstance(root, exp.Query):
        raise SqlRefused(
            f"Seules les requêtes de lecture sont autorisées, reçu {type(root).__name__}."
        )
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlRefused(f"Instruction interdite dans la requête : {type(node).__name__}.")


def _check_functions(root: exp.Expression) -> None:
    for func in root.find_all(exp.Anonymous):
        name = str(func.this).lower()
        if name.startswith(_FORBIDDEN_FUNCTION_PREFIXES) or name in _FORBIDDEN_FUNCTIONS:
            raise SqlRefused(f"Fonction interdite : {name}.")


def _check_tables(root: exp.Expression) -> None:
    # Une CTE est référencée comme une table ; ses noms sont donc légitimes.
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        schema = table.db.lower()
        if schema not in ALLOWED_SCHEMAS or table.catalog:
            raise SqlRefused(f"Schéma interdit : {table.sql(dialect=DIALECT)}.")
        name = table.name.lower()
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise SqlRefused(f"Table inconnue ou interdite : {name}.")


def _force_limit(root: exp.Query) -> exp.Query:
    limit = root.args.get("limit")
    if limit is None:
        return root.limit(MAX_ROWS)

    value = limit.expression
    if not (isinstance(value, exp.Literal) and value.is_int):
        raise SqlRefused("LIMIT doit être un entier littéral.")
    if int(value.name) > MAX_ROWS:
        return root.limit(MAX_ROWS)
    return root


def validate(sql: str) -> str:
    """Renvoie le SQL réécrit, prêt à exécuter, ou lève `SqlRefused`.

    Le SQL renvoyé n'est pas celui reçu : c'est celui qui sera réellement
    exécuté, `LIMIT` compris. C'est lui qui part dans `sources`, parce que citer
    la requête soumise plutôt que la requête exécutée serait une citation fausse.
    """
    root = _parse(sql)
    _check_readonly(root)
    _check_functions(root)
    _check_tables(root)
    return _force_limit(root).sql(dialect=DIALECT)


def run_query(sql: str) -> tuple[list[dict[str, Any]], str]:
    """Valide puis exécute. Renvoie les lignes et le SQL réellement exécuté.

    Toujours sur le moteur en lecture seule — jamais sur l'autre.
    """
    safe_sql = validate(sql)
    with get_ro_engine().begin() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
        result = conn.execute(text(safe_sql))
        rows = [dict(row) for row in result.mappings()]
    return rows, safe_sql
