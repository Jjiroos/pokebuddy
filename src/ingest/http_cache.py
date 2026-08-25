"""Client HTTP à cache disque.

La politique d'usage de PokéAPI n'impose aucune limite de débit mais **exige**
le cache local des ressources téléchargées, sous peine de bannissement d'IP.
Ce cache est donc une obligation de conformité, pas une optimisation.

Effet de bord utile : les tests d'ingestion tournent hors ligne, et une
réingestion complète est instantanée.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx


class CachedHTTP:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: httpx.AsyncClient,
        concurrency: int = 8,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._sem = asyncio.Semaphore(concurrency)
        self.hits = 0
        self.misses = 0

    def _path_for(self, url: str, body: dict[str, Any] | None = None) -> Path:
        # Le corps entre dans l'empreinte : une API GraphQL sert toutes ses
        # réponses depuis une URL unique, et les indexer sur la seule URL les
        # ferait toutes se répondre l'une l'autre.
        key = url if body is None else url + "\n" + json.dumps(body, sort_keys=True)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        # Éclaté sur deux niveaux : un seul dossier de dizaines de milliers de
        # fichiers se parcourt mal.
        return self.cache_dir / digest[:2] / f"{digest}.json"

    async def get_json(self, url: str) -> dict[str, Any]:
        path = self._path_for(url)
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text(encoding="utf-8"))

        async with self._sem:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()

        self.misses += 1
        self._store(path, data)
        return data

    async def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """Même cache, en POST — pour les API GraphQL.

        TCGdex n'expose `illustrator` en lot que par GraphQL : 25 requêtes au
        lieu de 20 000. Le cache reste une obligation de politesse envers une API
        communautaire, pas une optimisation.
        """
        path = self._path_for(url, body)
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text(encoding="utf-8"))

        async with self._sem:
            resp = await self._client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        if "errors" in data:
            raise RuntimeError(f"GraphQL a renvoyé une erreur : {data['errors']}")

        self.misses += 1
        self._store(path, data)
        return data

    def _store(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
