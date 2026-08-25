"""Transformations de l'ingestion TCGdex, sans réseau.

Les payloads sont des extraits réels, réduits aux champs que le code lit.
Deux sources s'y rejoignent : GraphQL, anglophone, qui porte `illustrator` ;
et le REST français, qui porte les noms que les questions emploient.
"""

from __future__ import annotations

from src.ingest.tcgdex import card_row, card_set_row, french_card_names

# GraphQL : la forme exacte renvoyée par api.tcgdex.net/v2/graphql.
CARTE_GQL = {
    "id": "base1-4",
    "localId": "4",
    "name": "Charizard",
    "illustrator": "Mitsuhiro Arita",
    "rarity": "Rare",
    "category": "Pokemon",
    "set": {"id": "base1", "name": "Base Set"},
}

# REST français : /v2/fr/sets/base1, réduit.
SET_FR = {
    "id": "base1",
    "name": "Set de Base",
    "serie": {"id": "base", "name": "Base"},
    "releaseDate": "1999-01-09",
    "cardCount": {"official": 102, "total": 102},
    "cards": [
        {"id": "base1-4", "localId": "4", "name": "Dracaufeu"},
        {"id": "base1-2", "localId": "2", "name": "Tortank"},
    ],
}


def test_les_deux_sources_se_rejoignent_sur_une_carte():
    """L'illustrateur vient de GraphQL, le nom français du REST.

    C'est tout l'objet de la double source : sans le français, une question qui
    parle de « la carte Dracaufeu » ne trouverait rien.
    """
    row = card_row(CARTE_GQL, french_card_names(SET_FR))
    assert row == {
        "id": "base1-4",
        "set_id": "base1",
        "local_id": "4",
        "name_en": "Charizard",
        "name_fr": "Dracaufeu",
        "illustrator": "Mitsuhiro Arita",
        "rarity": "Rare",
        "category": "Pokemon",
    }


def test_un_numero_non_numerique_ne_casse_pas_l_ingestion():
    """TCGdex numérote certaines cartes « ! » ou « ? » — `exu-!` existe.

    `local_id` est donc du texte : un entier ferait tomber l'ingestion entière
    sur ces quelques lignes.
    """
    carte = CARTE_GQL | {"id": "exu-!", "localId": "!", "set": {"id": "exu", "name": "Unown"}}
    assert card_row(carte, {})["local_id"] == "!"


def test_une_carte_sans_nom_francais_reste_ingerable():
    """Vingt extensions n'ont pas de localisation française. Les écarter
    perdrait leurs illustrateurs, qui sont la donnée qu'on vient chercher."""
    row = card_row(CARTE_GQL, {})
    assert row["name_fr"] is None
    assert row["illustrator"] == "Mitsuhiro Arita"


def test_l_extension_porte_ses_deux_noms():
    row = card_set_row("base1", "Base Set", SET_FR)
    assert row == {
        "id": "base1",
        "name_en": "Base Set",
        "name_fr": "Set de Base",
        "serie": "Base",
        "release_date": "1999-01-09",
        # `official` et non `total` : c'est le nombre que les questions citent.
        "card_count": 102,
    }


def test_une_extension_non_localisee_garde_son_nom_anglais():
    row = card_set_row("exu", "Unseen Forces Unown Collection", None)
    assert row["name_en"] == "Unseen Forces Unown Collection"
    assert row["name_fr"] is None
    assert row["serie"] is None


def test_les_cartes_sans_nom_sont_ignorees_de_l_index_francais():
    payload = SET_FR | {"cards": [{"id": "base1-9"}, {"id": "base1-4", "name": "Dracaufeu"}]}
    assert french_card_names(payload) == {"base1-4": "Dracaufeu"}
