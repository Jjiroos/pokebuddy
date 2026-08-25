"""Ingestion TCGdex vers les tables `card_sets` et `cards`.

Ce que cette ingestion débloque : les dix questions « illustrateur » de
l'évaluation, où le modèle nu plafonne à 20 %. C'est la connaissance la plus
absente de sa mémoire, donc celle qui se gagne le plus en allant la chercher.

**Deux sources pour une seule table, et c'est délibéré.** `illustrator` n'est
exposé en lot que par l'API GraphQL, anglophone : ~25 requêtes pour ~20 000
cartes, contre 20 000 en REST carte par carte. Mais les questions sont
françaises et parlent du « Set de Base », pas du « Base Set » : les noms
français viennent du REST, une requête par extension. Total ~225 appels, tous
mis en cache disque — politesse envers une API communautaire autant
qu'optimisation.

Comme pour PokéAPI, les transformations sont pures et testées sans réseau.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import func, select

from src.config import get_settings
from src.db.models import Card, CardSet
from src.db.session import get_sessionmaker
from src.ingest.http_cache import CachedHTTP
from src.ingest.write import chunks, upsert

GRAPHQL_URL = "https://api.tcgdex.net/v2/graphql"
REST_FR = "https://api.tcgdex.net/v2/fr"
PAGE_SIZE = 1000
# Garde-fou : une API qui renverrait indéfiniment des pages pleines ferait
# tourner la boucle sans fin.
MAX_PAGES = 100

CARDS_QUERY = """
query($page: Int!, $size: Int!) {
  cards(filters: {}, pagination: {page: $page, itemsPerPage: $size}) {
    id
    localId
    name
    illustrator
    rarity
    category
    set { id name }
  }
}
"""

log = logging.getLogger("ingest.tcgdex")


# --- transformations pures ------------------------------------------------


def french_card_names(set_payload: dict[str, Any]) -> dict[str, str]:
    """Noms français des cartes d'une extension, indexés par identifiant."""
    return {
        card["id"]: card["name"]
        for card in set_payload.get("cards") or []
        if card.get("id") and card.get("name")
    }


def card_set_row(set_id: str, name_en: str, fr_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Une extension. `fr_payload` peut manquer : toutes les extensions ne sont
    pas localisées, et une extension sans nom français reste utilisable."""
    fr = fr_payload or {}
    return {
        "id": set_id,
        "name_en": name_en,
        "name_fr": fr.get("name"),
        "serie": (fr.get("serie") or {}).get("name"),
        "release_date": fr.get("releaseDate"),
        "card_count": (fr.get("cardCount") or {}).get("official"),
    }


def card_row(card: dict[str, Any], french_names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": card["id"],
        "set_id": (card.get("set") or {}).get("id"),
        # `localId` est une chaîne : TCGdex numérote certaines cartes « ! » ou
        # « ? ». Un entier ferait tomber l'ingestion sur ces lignes.
        "local_id": str(card.get("localId") or ""),
        "name_en": card.get("name") or "",
        "name_fr": french_names.get(card["id"]),
        "illustrator": card.get("illustrator"),
        "rarity": card.get("rarity"),
        "category": card.get("category"),
    }


# --- orchestration --------------------------------------------------------


async def fetch_cards(http: CachedHTTP, *, limit: int | None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        payload = await http.post_json(
            GRAPHQL_URL,
            {"query": CARDS_QUERY, "variables": {"page": page, "size": PAGE_SIZE}},
        )
        batch = payload["data"]["cards"]
        cards += batch
        if len(batch) < PAGE_SIZE or (limit is not None and len(cards) >= limit):
            break
    else:
        log.warning("MAX_PAGES atteint : l'ingestion est peut-être incomplète.")
    return cards[:limit] if limit is not None else cards


async def fetch_french_sets(http: CachedHTTP, set_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Payloads français des extensions. Une extension absente n'est pas une
    erreur : elle sera simplement enregistrée sans nom français."""
    results = await asyncio.gather(
        *(http.get_json(f"{REST_FR}/sets/{set_id}") for set_id in set_ids),
        return_exceptions=True,
    )
    payloads: dict[str, dict[str, Any]] = {}
    manquants = 0
    for set_id, result in zip(set_ids, results, strict=True):
        if isinstance(result, BaseException):
            manquants += 1
            continue
        payloads[set_id] = result
    if manquants:
        log.info("%d extensions sans localisation française", manquants)
    return payloads


async def fetch_everything(http: CachedHTTP, *, limit: int | None) -> dict[str, Any]:
    cards = await fetch_cards(http, limit=limit)
    # Les identifiants d'extension viennent des cartes elles-mêmes : inutile de
    # lister les extensions séparément pour découvrir celles qui comptent.
    set_names = {
        c["set"]["id"]: c["set"].get("name") or c["set"]["id"] for c in cards if c.get("set")
    }
    french = await fetch_french_sets(http, sorted(set_names))
    return {"cards": cards, "set_names": set_names, "french_sets": french}


def load(session: Any, data: dict[str, Any]) -> dict[str, int]:
    set_names: dict[str, str] = data["set_names"]
    french: dict[str, dict[str, Any]] = data["french_sets"]

    set_rows = [card_set_row(sid, name, french.get(sid)) for sid, name in sorted(set_names.items())]
    counts = {"card_sets": 0, "cards": 0}
    for chunk in chunks(set_rows):
        counts["card_sets"] += upsert(session, CardSet, chunk, keys=["id"])
    session.flush()

    french_names: dict[str, str] = {}
    for payload in french.values():
        french_names |= french_card_names(payload)

    rows = [card_row(c, french_names) for c in data["cards"]]
    # Une carte dont l'extension est inconnue violerait la clé étrangère. Ça
    # n'arrive qu'en ingestion partielle, mais autant que ce soit explicite.
    connus = set(set_names)
    rows = [r for r in rows if r["set_id"] in connus]
    for chunk in chunks(rows):
        counts["cards"] += upsert(session, Card, chunk, keys=["id"])

    return counts


async def run(limit: int | None = None) -> dict[str, int]:
    settings = get_settings()
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "pokebuddy/0.1"}) as c:
        http = CachedHTTP(settings.tcgdex_cache_dir, client=c)
        data = await fetch_everything(http, limit=limit)
        log.info("cache TCGdex : %d servis, %d téléchargés", http.hits, http.misses)

    with get_sessionmaker()() as session:
        counts = load(session, data)
        session.commit()

    with get_sessionmaker()() as session:
        counts["illustrateurs_distincts"] = (
            session.scalar(select(func.count(func.distinct(Card.illustrator)))) or 0
        )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion TCGdex -> PostgreSQL")
    parser.add_argument(
        "--limit", type=int, default=None, help="ne traiter que les N premières cartes"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for key, value in asyncio.run(run(args.limit)).items():
        log.info("%s : %d", key, value)


if __name__ == "__main__":
    main()
