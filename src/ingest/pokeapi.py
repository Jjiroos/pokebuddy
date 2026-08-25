"""Ingestion PokéAPI vers les tables relationnelles.

Les fonctions de transformation sont pures et séparées des entrées/sorties :
les tests les alimentent avec des réponses enregistrées, sans réseau ni base.

Idempotence : tous les écritures sont des upserts. Relancer l'ingestion ne
duplique rien et met à jour ce qui a changé.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import (
    Pokemon,
    PokemonGameAppearance,
    PokemonType,
    Species,
    Type,
    Version,
)
from src.db.session import get_sessionmaker
from src.ingest.http_cache import CachedHTTP
from src.ingest.write import chunks, upsert

BASE_URL = "https://pokeapi.co/api/v2"

_ROMAN = {"i": 1, "v": 5, "x": 10}

log = logging.getLogger("ingest.pokeapi")


# --- transformations pures ------------------------------------------------


def id_from_url(url: str) -> int:
    """`https://pokeapi.co/api/v2/pokemon-species/25/` -> 25."""
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def parse_generation(name: str | None) -> int | None:
    """`generation-vii` -> 7."""
    if not name or "-" not in name:
        return None
    roman = name.rsplit("-", 1)[-1].lower()
    total, previous = 0, 0
    for char in reversed(roman):
        value = _ROMAN.get(char)
        if value is None:
            return None
        total += value if value >= previous else -value
        previous = max(previous, value)
    return total or None


def localized_name(payload: dict[str, Any], language: str) -> str | None:
    """Nom localisé, depuis `names` du payload — déjà présent dans le cache.

    Aucun appel réseau supplémentaire : la ressource est la même que celle qui
    portait déjà les statistiques.
    """
    for entry in payload.get("names") or []:
        if (entry.get("language") or {}).get("name") == language:
            return entry.get("name")
    return None


def species_row(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload["id"],
        "name": payload["name"],
        "name_fr": localized_name(payload, "fr"),
        "generation": parse_generation((payload.get("generation") or {}).get("name")),
    }


def species_parent_id(payload: dict[str, Any]) -> int | None:
    parent = payload.get("evolves_from_species")
    return id_from_url(parent["url"]) if parent else None


def national_dex_number(species_payload: dict[str, Any]) -> int | None:
    """Lu depuis `pokedex_numbers`, et non déduit de l'id de l'espèce.

    Les deux coïncident aujourd'hui pour la série principale, mais s'appuyer
    sur cette coïncidence serait une hypothèse tacite qu'aucun test ne garde.
    """
    for entry in species_payload.get("pokedex_numbers", []):
        if (entry.get("pokedex") or {}).get("name") == "national":
            return entry.get("entry_number")
    return None


def pokemon_row(payload: dict[str, Any], *, national_dex_number: int | None) -> dict[str, Any]:
    # PokéAPI nomme les stats en kebab-case ; les colonnes sont en snake_case.
    stats = {s["stat"]["name"].replace("-", "_"): s["base_stat"] for s in payload.get("stats", [])}
    return {
        "id": payload["id"],
        "species_id": id_from_url(payload["species"]["url"]),
        "name": payload["name"],
        "national_dex_number": national_dex_number,
        "is_default": bool(payload.get("is_default", True)),
        "height_dm": payload.get("height"),
        "weight_hg": payload.get("weight"),
        "hp": stats.get("hp"),
        "attack": stats.get("attack"),
        "defense": stats.get("defense"),
        "special_attack": stats.get("special_attack"),
        "special_defense": stats.get("special_defense"),
        "speed": stats.get("speed"),
    }


def pokemon_type_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "pokemon_id": payload["id"],
            "type_id": id_from_url(entry["type"]["url"]),
            "slot": entry["slot"],
        }
        for entry in payload.get("types", [])
    ]


def appearance_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Apparitions par jeu, depuis `game_indices`.

    Ce champ est renseigné pour les générations I à VII et vide au-delà : la
    lacune vient de la source. Les questions d'évaluation du jalon 2 portant
    sur les jeux récents devront en tenir compte.
    """
    return [
        {"pokemon_id": payload["id"], "version_id": id_from_url(entry["version"]["url"])}
        for entry in payload.get("game_indices", [])
    ]


def named_resource_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Transforme une liste paginée `{results: [{name, url}]}` en lignes id/nom."""
    return [{"id": id_from_url(r["url"]), "name": r["name"]} for r in payload["results"]]


def type_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Un type, avec son nom français.

    Vingt-et-une ressources à récupérer une à une plutôt que la liste paginée :
    `names` n'existe que sur la ressource détaillée, et sans lui une question
    posée en français ne peut pas filtrer sur un type.
    """
    return {
        "id": payload["id"],
        "name": payload["name"],
        "name_fr": localized_name(payload, "fr"),
    }


# --- écriture -------------------------------------------------------------


def parent_updates(
    species_payloads: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, int]], int]:
    """Liens d'évolution posables, et nombre de liens écartés.

    Un lien n'est retenu que si l'espèce parente fait partie du lot ingéré :
    sur une ingestion partielle, poser le lien violerait la clé étrangère.
    """
    connus = {s["id"] for s in species_payloads}
    liens, orphelins = [], 0
    for payload in species_payloads:
        parent = species_parent_id(payload)
        if parent is None:
            continue
        if parent in connus:
            liens.append({"id": payload["id"], "parent": parent})
        else:
            orphelins += 1
    return liens, orphelins


# --- orchestration --------------------------------------------------------


async def fetch_everything(http: CachedHTTP, *, limit: int | None) -> dict[str, Any]:
    types, versions, index = await asyncio.gather(
        http.get_json(f"{BASE_URL}/type?limit=1000"),
        http.get_json(f"{BASE_URL}/version?limit=1000"),
        http.get_json(f"{BASE_URL}/pokemon?limit=100000"),
    )
    entries = index["results"][:limit] if limit else index["results"]
    log.info("%d pokémon à récupérer", len(entries))

    pokemon = await asyncio.gather(*(http.get_json(e["url"]) for e in entries))

    species_urls = {p["species"]["url"] for p in pokemon}
    species = await asyncio.gather(*(http.get_json(u) for u in sorted(species_urls)))

    type_details = await asyncio.gather(*(http.get_json(t["url"]) for t in types["results"]))

    return {
        "types": type_details,
        "versions": versions,
        "pokemon": pokemon,
        "species": species,
    }


def load(session: Session, data: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}

    counts["types"] = upsert(session, Type, [type_row(t) for t in data["types"]], keys=["id"])
    counts["versions"] = upsert(
        session, Version, named_resource_rows(data["versions"]), keys=["id"]
    )

    # Deux passes sur les espèces : l'auto-référence evolves_from_species_id ne
    # peut pointer que vers une ligne déjà insérée.
    species_payloads = data["species"]
    counts["species"] = 0
    for chunk in chunks([species_row(s) for s in species_payloads]):
        counts["species"] += upsert(session, Species, chunk, keys=["id"])
    session.flush()

    # Le second passage est un UPDATE, pas un upsert. PostgreSQL valide les
    # contraintes NOT NULL en formant le tuple, donc *avant* de détecter le
    # conflit d'unicité : un « INSERT (id, evolves_from) ... ON CONFLICT DO
    # UPDATE » échouerait sur species.name même quand la ligne existe déjà.
    parents, orphelins = parent_updates(species_payloads)
    if parents:
        # UPDATE en masse par clé primaire : SQLAlchemy déduit le WHERE de la
        # présence de « id » dans chaque dictionnaire.
        session.execute(
            update(Species),
            [{"id": r["id"], "evolves_from_species_id": r["parent"]} for r in parents],
        )
    counts["evolutions"] = len(parents)
    if orphelins:
        # Attendu sur une ingestion partielle (--limit) : le parent d'un Pokémon
        # peut se trouver hors du lot. On ne pose pas un lien qui violerait la
        # clé étrangère, et on le dit.
        log.info("%d évolutions ignorées : espèce parente hors du lot", orphelins)
    session.flush()

    # Le numéro du Pokédex national vit sur l'espèce, pas sur la forme : les
    # formes régionales le partagent avec leur espèce d'origine.
    dex_by_species = {s["id"]: national_dex_number(s) for s in species_payloads}
    pokemon_rows = [
        pokemon_row(p, national_dex_number=dex_by_species.get(id_from_url(p["species"]["url"])))
        for p in data["pokemon"]
    ]
    counts["pokemon"] = 0
    for chunk in chunks(pokemon_rows):
        counts["pokemon"] += upsert(session, Pokemon, chunk, keys=["id"])
    session.flush()

    type_rows = [r for p in data["pokemon"] for r in pokemon_type_rows(p)]
    counts["pokemon_types"] = 0
    for chunk in chunks(type_rows):
        counts["pokemon_types"] += upsert(
            session, PokemonType, chunk, keys=["pokemon_id", "type_id"]
        )

    appearances = [r for p in data["pokemon"] for r in appearance_rows(p)]
    # `game_indices` peut répéter une version pour une même forme.
    unique = list({(r["pokemon_id"], r["version_id"]): r for r in appearances}.values())
    counts["appearances"] = 0
    for chunk in chunks(unique):
        counts["appearances"] += upsert(
            session, PokemonGameAppearance, chunk, keys=["pokemon_id", "version_id"]
        )

    return counts


async def run(limit: int | None = None) -> dict[str, int]:
    settings = get_settings()
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "pokebuddy/0.1"}) as c:
        http = CachedHTTP(settings.pokeapi_cache_dir, client=c)
        data = await fetch_everything(http, limit=limit)
        log.info("cache PokéAPI : %d servis, %d téléchargés", http.hits, http.misses)

    with get_sessionmaker()() as session:
        counts = load(session, data)
        session.commit()

    with get_sessionmaker()() as session:
        counts["total_pokemon_en_base"] = (
            session.scalar(select(func.count()).select_from(Pokemon)) or 0
        )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion PokéAPI -> PostgreSQL")
    parser.add_argument(
        "--limit", type=int, default=None, help="ne traiter que les N premiers pokémon"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    counts = asyncio.run(run(args.limit))
    for key, value in counts.items():
        log.info("%-22s %d", key, value)


if __name__ == "__main__":
    main()
