"""Ce que le validateur SQL arrête.

Chaque test porte le nom du contournement qu'il ferme. C'est le fichier le plus
important du jalon 3 : il exécute du SQL écrit par un modèle, donc par un
inconnu, et la question n'est pas « est-ce que ça marche » mais « qu'est-ce qui
passe quand ça ne devrait pas ».

Aucun test ne touche la base : `validate()` est une fonction pure. L'exécution
réelle est couverte par le rôle PostgreSQL en lecture seule, vérifié à la main
et documenté dans le README — c'est une propriété du serveur, pas du code.
"""

from __future__ import annotations

import pytest

from src.tools.sql import MAX_ROWS, SqlRefused, validate

# --- ce qui doit être refusé ---------------------------------------------


def test_deux_instructions_sont_refusees():
    """Le classique : une lecture anodine suivie d'une destruction."""
    with pytest.raises(SqlRefused, match="instructions"):
        validate("SELECT 1; DROP TABLE cards")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM pokemon",
        "UPDATE pokemon SET hp = 1",
        "INSERT INTO types (id, name) VALUES (99, 'x')",
        "DROP TABLE cards",
        "ALTER TABLE cards ADD COLUMN x int",
        "TRUNCATE TABLE cards",
        "GRANT ALL ON pokemon TO public",
    ],
)
def test_toute_ecriture_est_refusee(sql):
    with pytest.raises(SqlRefused):
        validate(sql)


def test_une_cte_ecrivante_est_refusee():
    """Le contournement élégant : l'expression racine est bien un SELECT.

    Un validateur qui ne regarderait que le premier nœud laisserait passer
    celui-ci. C'est la raison pour laquelle l'arbre entier est parcouru.
    """
    with pytest.raises(SqlRefused, match="Insert"):
        validate("WITH x AS (INSERT INTO types VALUES (1, 'a') RETURNING *) SELECT * FROM x")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT * FROM information_schema.tables",
    ],
)
def test_les_schemas_systeme_sont_refuses(sql):
    """Ni `pg_catalog` ni `information_schema` ne sont nommés dans le code : ils
    tombent parce que seul `public` est autorisé."""
    with pytest.raises(SqlRefused, match="Schéma interdit"):
        validate(sql)


def test_une_table_hors_liste_blanche_est_refusee():
    with pytest.raises(SqlRefused, match="secrets"):
        validate("SELECT * FROM secrets")


def test_les_fonctions_de_lecture_de_fichier_sont_refusees():
    with pytest.raises(SqlRefused, match="pg_read_file"):
        validate("SELECT pg_read_file('/etc/passwd')")


def test_une_instruction_non_reconnue_est_refusee():
    """`VACUUM` n'est pas typé par sqlglot et atterrit en `Command`.

    Refuser ce qu'on n'a pas compris est le comportement correct d'un
    validateur ; laisser passer l'inconnu serait l'inverse.
    """
    with pytest.raises(SqlRefused):
        validate("VACUUM FULL")


def test_un_limit_non_litteral_est_refuse():
    with pytest.raises(SqlRefused, match="entier littéral"):
        validate("SELECT name FROM pokemon LIMIT (SELECT count(*) FROM types)")


# --- ce qui doit passer, et comment ---------------------------------------


def test_un_limit_absent_est_impose():
    assert validate("SELECT name FROM pokemon").endswith(f"LIMIT {MAX_ROWS}")


def test_un_limit_excessif_est_ramene_au_plafond():
    assert validate("SELECT name FROM pokemon LIMIT 5000").endswith(f"LIMIT {MAX_ROWS}")


def test_un_limit_raisonnable_est_conserve():
    assert validate("SELECT name FROM pokemon LIMIT 10").endswith("LIMIT 10")


def test_un_commentaire_ne_neutralise_pas_le_limit():
    """Un `--` en fin de ligne mettrait le LIMIT ajouté en commentaire si on
    l'accolait par concaténation. La réécriture d'arbre l'en empêche."""
    reecrit = validate("SELECT 1 -- ; DROP TABLE cards")
    assert reecrit.endswith(f"LIMIT {MAX_ROWS}")
    assert "--" not in reecrit


def test_une_cte_de_lecture_passe():
    """Une CTE est référencée comme une table : ses noms doivent être admis
    sans pour autant ouvrir la liste blanche."""
    assert "LIMIT" in validate("WITH t AS (SELECT id FROM types) SELECT count(*) FROM t")


def test_une_union_passe():
    assert validate("SELECT name FROM pokemon UNION SELECT name FROM species")


def test_une_jointure_realiste_passe():
    """La forme exacte qu'appelle une question d'agrégation de l'évaluation."""
    sql = """
        SELECT s.name_fr, p.speed
        FROM pokemon p
        JOIN species s ON s.id = p.species_id
        JOIN pokemon_types pt ON pt.pokemon_id = p.id
        JOIN types t ON t.id = pt.type_id
        WHERE t.name = 'water' AND p.speed > 130 AND p.is_default
    """
    assert validate(sql).endswith(f"LIMIT {MAX_ROWS}")
