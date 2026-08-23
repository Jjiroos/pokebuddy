"""Cache disque des appels LLM, et grand livre des dépenses.

Deux rôles dans un seul fichier SQLite :

* `llm_cache` — rejouer l'évaluation gratuitement et à l'identique. C'est ce qui
  remplace `temperature=0` comme garantie de reproductibilité, la famille GPT-5
  ne l'acceptant plus.
* `llm_calls` — journal de chaque invocation, cache compris. Sert à publier un
  coût honnête plutôt qu'une estimation.

SQLite plutôt qu'une arborescence de fichiers JSON : un seul fichier tient
mieux la charge qu'un millier de petits fichiers, en particulier hors d'ext4.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key           TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL,
    provider            TEXT    NOT NULL,
    model               TEXT    NOT NULL,
    cache_key           TEXT    NOT NULL,
    cache_hit           INTEGER NOT NULL,
    input_tokens        INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    cost_usd            REAL    NOT NULL,
    latency_ms          INTEGER NOT NULL,
    run_label           TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run_label ON llm_calls(run_label);
"""


def cache_key(
    *,
    provider: str,
    model: str,
    messages: Sequence[dict[str, Any]],
    params: dict[str, Any],
    schema_fingerprint: str | None,
) -> str:
    """Empreinte stable d'un appel.

    Tout ce qui peut changer la réponse entre dans la clé — y compris le schéma
    de sortie, sans quoi deux extractions de formes différentes se répondraient
    l'une l'autre.
    """
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "messages": [dict(m) for m in messages],
            "params": params,
            "schema": schema_fingerprint,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False : FastAPI sert les routes synchrones depuis un
        # pool de threads. Le verrou sérialise les accès.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json FROM llm_cache WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["response_json"]) if row else None

    def put(self, key: str, *, provider: str, model: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache "
                "(key, provider, model, response_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    provider,
                    model,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._conn.commit()

    def log_call(
        self,
        *,
        provider: str,
        model: str,
        key: str,
        cache_hit: bool,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        run_label: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_calls (created_at, provider, model, cache_key, cache_hit,"
                " input_tokens, cached_input_tokens, output_tokens, cost_usd, latency_ms,"
                " run_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    provider,
                    model,
                    key,
                    int(cache_hit),
                    input_tokens,
                    cached_input_tokens,
                    output_tokens,
                    cost_usd,
                    latency_ms,
                    run_label,
                ),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
