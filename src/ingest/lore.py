"""Ingestion des entrées de Pokédex, et leurs plongements.

Le corpus n'est pas allé se chercher ailleurs : **il dormait déjà dans le cache
d'ingestion**. Chaque payload `pokemon-species` de PokéAPI porte ses
`flavor_text_entries`, localisées, que le jalon 1 téléchargeait sans les lire.
Ni scraping, ni nouvelle licence, ni appel réseau — et le compteur du cache le
prouve à chaque run plutôt que de le promettre.

Une entrée **est** un document. Ces textes font deux ou trois phrases : les
découper ajouterait un paramètre à régler sans rien améliorer.

Le modèle de plongement est multilingue et tourne en ONNX (`fastembed`), donc
sans torch ni GPU. Le corpus est en français, un modèle anglophone n'y
comprendrait rien.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
from typing import Any

import httpx
from sqlalchemy import func, select

from src.config import get_settings
from src.db.models import EMBEDDING_DIM, LoreChunk, Species
from src.db.session import get_sessionmaker
from src.ingest.http_cache import CachedHTTP
from src.ingest.write import chunks, upsert

BASE_URL = "https://pokeapi.co/api/v2"
LANGUAGE = "fr"
# 384 dimensions, 220 Mo, multilingue. Les deux autres modèles multilingues de
# fastembed pèsent 1 Go et 2,24 Go pour un corpus de 5 000 phrases courtes :
# le rapport qualité/poids ne le justifie pas.
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_BATCH = 256

_ESPACES = re.compile(r"\s+")

log = logging.getLogger("ingest.lore")


# --- transformations pures ------------------------------------------------


def clean(text: str) -> str:
    """PokéAPI conserve les sauts de ligne et les sauts de page du jeu d'origine.

    Les garder ferait diverger deux entrées identiques à la mise en page près,
    et polluerait le texte envoyé au modèle de plongement.
    """
    return _ESPACES.sub(" ", text).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def french_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Entrées françaises d'une espèce, dédoublonnées.

    La même entrée est reprise à l'identique d'une version à l'autre d'un même
    jeu ; on garde la première version qui l'a portée.
    """
    vus: set[str] = set()
    rows: list[dict[str, Any]] = []
    for entry in payload.get("flavor_text_entries") or []:
        if (entry.get("language") or {}).get("name") != LANGUAGE:
            continue
        texte = clean(entry.get("flavor_text") or "")
        if not texte:
            continue
        empreinte = text_hash(texte)
        if empreinte in vus:
            continue
        vus.add(empreinte)
        rows.append(
            {
                "species_id": payload["id"],
                "version": (entry.get("version") or {}).get("name") or "inconnue",
                "text": texte,
                "text_hash": empreinte,
            }
        )
    return rows


# --- orchestration --------------------------------------------------------


def species_ids(session: Any, limit: int | None) -> list[int]:
    """Les espèces déjà en base. `make ingest` doit avoir tourné avant.

    Partir de la base plutôt que d'une liste PokéAPI garantit qu'aucun chunk ne
    référencera une espèce absente — la clé étrangère ne laisserait pas passer.
    """
    stmt = select(Species.id).order_by(Species.id)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


async def fetch_entries(http: CachedHTTP, ids: list[int]) -> list[dict[str, Any]]:
    payloads = await asyncio.gather(
        *(http.get_json(f"{BASE_URL}/pokemon-species/{i}/") for i in ids)
    )
    return [row for p in payloads for row in french_entries(p)]


def embed(rows: list[dict[str, Any]]) -> None:
    """Ajoute `embedding` à chaque ligne, sur place.

    Import local : `fastembed` charge onnxruntime et un modèle de 220 Mo. Ce
    coût n'a aucune raison d'être payé par l'API, qui n'ingère jamais.
    """
    from fastembed import TextEmbedding

    settings = get_settings()
    model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(settings.fastembed_cache_dir))
    vecteurs = model.embed([r["text"] for r in rows], batch_size=EMBED_BATCH)
    for row, vecteur in zip(rows, vecteurs, strict=True):
        vecteur = vecteur.tolist()
        if len(vecteur) != EMBEDDING_DIM:
            raise ValueError(
                f"Le modèle rend {len(vecteur)} dimensions, la colonne en attend "
                f"{EMBEDDING_DIM}. Changer de modèle impose une migration."
            )
        row["embedding"] = vecteur


async def run(limit: int | None = None) -> dict[str, int]:
    settings = get_settings()

    with get_sessionmaker()() as session:
        ids = species_ids(session, limit)
    if not ids:
        raise RuntimeError("Aucune espèce en base : lancer `make ingest` d'abord.")

    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "pokebuddy/0.1"}) as c:
        http = CachedHTTP(settings.pokeapi_cache_dir, client=c)
        rows = await fetch_entries(http, ids)
        # Le chiffre qui rend la promesse vérifiable : sur un cache chaud, il
        # doit valoir zéro téléchargement.
        log.info("cache PokéAPI : %d servis, %d téléchargés", http.hits, http.misses)

    log.info("%d entrées françaises à plonger", len(rows))
    embed(rows)

    counts = {"chunks": 0}
    with get_sessionmaker()() as session:
        for chunk in chunks(rows, size=500):
            counts["chunks"] += upsert(session, LoreChunk, chunk, keys=["species_id", "text_hash"])
        session.commit()

    with get_sessionmaker()() as session:
        counts["especes_couvertes"] = (
            session.scalar(select(func.count(func.distinct(LoreChunk.species_id)))) or 0
        )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des entrées de Pokédex -> pgvector")
    parser.add_argument(
        "--limit", type=int, default=None, help="ne traiter que les N premières espèces"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for key, value in asyncio.run(run(args.limit)).items():
        log.info("%s : %d", key, value)


if __name__ == "__main__":
    main()
