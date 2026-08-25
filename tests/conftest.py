"""Fixtures communes.

Les variables d'environnement sont posées avant tout import applicatif :
`get_settings` est mis en cache, et le cache LLM doit atterrir dans un dossier
jetable plutôt que dans le ~/.cache du développeur.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pokebuddy-tests-"))
os.environ["OPENAI_API_KEY"] = "sk-test-jamais-utilisee"
os.environ["LLM_CACHE_PATH"] = str(_TMP / "llm.sqlite")
os.environ["POKEAPI_CACHE_DIR"] = str(_TMP / "pokeapi")
os.environ["DATABASE_URL"] = "postgresql+psycopg://pokebuddy:pokebuddy@localhost:5432/pokebuddy"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.api.deps import get_llm  # noqa: E402
from src.api.main import create_app  # noqa: E402
from src.api.schemas import SqlPlan  # noqa: E402
from src.llm.provider import LLMResponse, Message  # noqa: E402


class FakeProvider:
    """Double du fournisseur. Aucun test du projet ne sort sur le réseau.

    Depuis le jalon 3, `/ask` appelle le modèle deux fois : d'abord pour un
    `SqlPlan`, puis pour la réponse. `sql_plan` pilote le premier appel ; laissé
    à None, il fait répondre « pas de SQL », ce qui replie sur le chemin nu du
    jalon 1 — celui que la plupart de ces tests vérifient.
    """

    name = "fake"
    model = "gpt-5.6-luna"

    def __init__(
        self,
        payloads: list[dict] | None = None,
        refusal: str | None = None,
        sql_plan: dict | None = None,
    ) -> None:
        self._payloads = payloads or []
        self._refusal = refusal
        self._sql_plan = sql_plan or {"sql": None, "reason": "double de test"}
        self.calls: list[tuple[list[Message], type[BaseModel] | None]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None = None,
        run_label: str | None = None,
    ) -> LLMResponse:
        self.calls.append(([dict(m) for m in messages], schema))
        parsed = None
        if self._refusal is None and schema is SqlPlan:
            parsed = SqlPlan.model_validate(self._sql_plan)
        elif schema is not None and self._payloads and self._refusal is None:
            parsed = schema.model_validate(self._payloads.pop(0))
        return LLMResponse(
            text="texte brut",
            model=self.model,
            input_tokens=120,
            cached_input_tokens=0,
            output_tokens=42,
            cost_usd=0.00007,
            latency_ms=350,
            cache_hit=False,
            parsed=parsed,
            refusal=self._refusal,
        )


@pytest.fixture
def make_client():
    """Fabrique un client HTTP branché sur un fournisseur factice donné."""

    def _make(provider: FakeProvider) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_llm] = lambda: provider
        return TestClient(app)

    return _make


@pytest.fixture
def tmp_cache_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.sqlite"
