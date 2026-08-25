"""Tests des transformations d'ingestion, sur des réponses PokéAPI réelles.

Aucun réseau, aucune base : les fonctions de `src.ingest.pokeapi` visées ici
sont pures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.pokeapi import (
    appearance_rows,
    id_from_url,
    named_resource_rows,
    national_dex_number,
    parse_generation,
    pokemon_row,
    pokemon_type_rows,
    species_parent_id,
    species_row,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pokeapi"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def charizard():
    return load("pokemon_charizard.json")


@pytest.fixture
def raichu_alola():
    return load("pokemon_raichu_alola.json")


@pytest.fixture
def species_raichu():
    return load("species_raichu.json")


@pytest.fixture
def species_charizard():
    return load("species_charizard.json")


# --- utilitaires ----------------------------------------------------------


def test_id_depuis_url():
    assert id_from_url("https://pokeapi.co/api/v2/pokemon-species/25/") == 25
    assert id_from_url("https://pokeapi.co/api/v2/type/10") == 10


@pytest.mark.parametrize(
    ("nom", "attendu"),
    [
        ("generation-i", 1),
        ("generation-iv", 4),
        ("generation-vii", 7),
        ("generation-ix", 9),
        (None, None),
        ("bizarre", None),
    ],
)
def test_generation_en_chiffres_romains(nom, attendu):
    assert parse_generation(nom) == attendu


def test_liste_paginee_vers_lignes():
    payload = {"results": [{"name": "fire", "url": "https://pokeapi.co/api/v2/type/10/"}]}
    assert named_resource_rows(payload) == [{"id": 10, "name": "fire"}]


# --- espèces --------------------------------------------------------------


def test_espece(species_charizard):
    """`name_fr` vient du même payload que le reste : aucun appel de plus.

    C'est ce qui permet au générateur de SQL du jalon 3 de filtrer sur
    « Dracaufeu » sans traduire de mémoire."""
    assert species_row(species_charizard) == {
        "id": 6,
        "name": "charizard",
        "name_fr": "Dracaufeu",
        "generation": 1,
    }


def test_parent_d_evolution(species_raichu, species_charizard):
    assert species_parent_id(species_raichu) == 25  # Pikachu
    assert species_parent_id(species_charizard) == 5  # Reptincel


def test_dex_national_lu_et_non_deduit(species_raichu):
    assert national_dex_number(species_raichu) == 26


# --- pokémon --------------------------------------------------------------


def test_stats_en_colonnes_larges(charizard):
    row = pokemon_row(charizard, national_dex_number=6)
    assert row["hp"] == 78
    assert row["attack"] == 84
    assert row["special_attack"] == 109
    assert row["speed"] == 100
    # kebab-case côté API, snake_case côté colonnes
    assert "special-attack" not in row


def test_forme_par_defaut(charizard):
    row = pokemon_row(charizard, national_dex_number=6)
    assert row["id"] == 6
    assert row["is_default"] is True
    assert row["species_id"] == 6
    assert row["height_dm"] == 17
    assert row["weight_hg"] == 905


def test_forme_regionale_distincte_mais_meme_espece(raichu_alola):
    """Le piège de l'évaluation : Raichu d'Alola est une ligne à part, avec ses
    propres types, rattachée à l'espèce Raichu et partageant son numéro de dex."""
    row = pokemon_row(raichu_alola, national_dex_number=26)
    assert row["id"] == 10100
    assert row["name"] == "raichu-alola"
    assert row["is_default"] is False
    assert row["species_id"] == 26
    assert row["national_dex_number"] == 26


def test_types_ordonnes_par_slot(charizard, raichu_alola):
    assert pokemon_type_rows(charizard) == [
        {"pokemon_id": 6, "type_id": 10, "slot": 1},  # feu
        {"pokemon_id": 6, "type_id": 3, "slot": 2},  # vol
    ]
    # La forme d'Alola change les types : électrik / psy, pas électrik seul.
    alola = pokemon_type_rows(raichu_alola)
    assert [r["slot"] for r in alola] == [1, 2]
    assert {r["type_id"] for r in alola} == {13, 14}


def test_apparitions_par_jeu(charizard):
    rows = appearance_rows(charizard)
    assert len(rows) == 46
    assert all(r["pokemon_id"] == 6 for r in rows)
    assert {"pokemon_id": 6, "version_id": 1} in rows  # Rouge


def test_les_generations_recentes_n_ont_pas_d_apparitions(raichu_alola):
    """`game_indices` est vide au-delà de la génération VII : la lacune est dans
    la source, pas dans l'ingestion. À retenir pour les questions du jalon 2."""
    assert appearance_rows(raichu_alola) == []


# --- liens d'évolution ----------------------------------------------------


def test_les_parents_hors_du_lot_sont_ecartes(species_raichu, species_charizard):
    """Sur une ingestion partielle, Pikachu (25) peut être absent du lot alors
    que Raichu y est. Poser le lien violerait la clé étrangère."""
    from src.ingest.pokeapi import parent_updates

    liens, orphelins = parent_updates([species_raichu, species_charizard])
    assert liens == []
    assert orphelins == 2


def test_un_parent_present_dans_le_lot_est_conserve(species_raichu):
    from src.ingest.pokeapi import parent_updates

    pikachu = {"id": 25, "name": "pikachu", "generation": {"name": "generation-i"}}
    liens, orphelins = parent_updates([species_raichu, pikachu])
    assert liens == [{"id": 26, "parent": 25}]
    assert orphelins == 0
