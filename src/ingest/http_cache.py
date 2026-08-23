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

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
