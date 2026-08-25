"""Exécution de l'évaluation contre la vraie route `/ask`.

L'application est instanciée en-process et servie par `ASGITransport` : pas de
serveur à démarrer, et surtout **aucune réimplémentation de l'invite système**.
On mesure le produit, pas une copie du produit — ce qui rend les runs des
jalons 3 et 4 comparables à celui-ci sans toucher à ce fichier.

`ASGITransport` ne déclenche pas le `lifespan`, ce qui tombe bien : c'est le
point où le fournisseur étiqueté est injecté.

    python -m eval.runner [--model M] [--persona pokedex|factual|both] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from eval.grader import grade
from eval.report import render_markdown
from src.api.main import create_app
from src.api.schemas import Persona
from src.llm.factory import get_provider
from src.llm.provider import LLMProvider, LLMResponse, Message

QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"
RUNS_DIR = Path(__file__).parent / "runs"

# Le cache SQLite sérialise ses écritures, et les paliers gratuits plafonnent en
# tokens par minute autant qu'en requêtes : celui de Groq autorise 30 req/min
# mais 8 000 tokens/min, soit une dizaine d'appels. Le provider rejoue les 429,
# mais un run qui passe son temps en backoff ne mesure plus la latence — deux
# requêtes en vol tiennent sous les deux plafonds.
CONCURRENCY = 2
REQUEST_TIMEOUT_S = 180.0


class LabelledProvider:
    """Fournisseur qui étiquette chaque appel du run qui l'a produit.

    Implémente le `Protocol` de `src/llm/provider.py` et délègue. C'est ce qui
    rend les lignes de `llm_calls` attribuables à un run — sans ajouter un champ
    `run_label` à `AskRequest`, qui n'a rien à faire dans l'API publique.
    """

    def __init__(self, inner: LLMProvider, label: str) -> None:
        self._inner = inner
        self._label = label
        self.name = inner.name
        self.model = inner.model

    def complete(
        self,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None = None,
        run_label: str | None = None,
    ) -> LLMResponse:
        return self._inner.complete(messages, schema=schema, run_label=run_label or self._label)


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def questions_digest(path: Path = QUESTIONS_PATH) -> str:
    """Empreinte du jeu de questions, embarquée dans l'artefact de run.

    Un tableau de résultats sans l'empreinte des questions qui l'ont produit
    n'est pas vérifiable.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _failure(question: dict[str, Any], persona: str, cause: str) -> dict[str, Any]:
    """Une question qui échoue est un résultat faux, jamais une exception.

    Un run doit toujours produire son artefact : perdre 79 réponses payées
    parce que la 80e a pris un 502 serait absurde.
    """
    return {
        "id": question["id"],
        "category": question["category"],
        "persona": persona,
        "question": question["question"],
        "expected": question["expected"],
        "answer": "",
        "confidence": None,
        "ok": False,
        "detail": cause,
        "error": cause,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
        "cache_hit": False,
    }


async def _ask_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    question: dict[str, Any],
    persona: str,
) -> dict[str, Any]:
    payload = {"question": question["question"], "persona": persona}
    async with semaphore:
        try:
            response = await client.post("/ask", json=payload, timeout=REQUEST_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — tout échec est un résultat, pas un arrêt
            return _failure(question, persona, f"{type(exc).__name__}: {exc}")

    if response.status_code != 200:
        return _failure(question, persona, f"HTTP {response.status_code} — {response.text[:200]}")

    body = response.json()
    verdict = grade(body["answer"], question["check"])
    usage = body["usage"]
    return {
        "id": question["id"],
        "category": question["category"],
        "persona": persona,
        "question": question["question"],
        "expected": question["expected"],
        "answer": body["answer"],
        "confidence": body["confidence"],
        "ok": verdict.ok,
        "detail": verdict.detail,
        "error": None,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost_usd": usage["cost_usd"],
        "latency_ms": usage["latency_ms"],
        "cache_hit": usage["cache_hit"],
    }


async def _run_persona(
    provider: LLMProvider,
    questions: list[dict[str, Any]],
    persona: str,
    label: str,
) -> list[dict[str, Any]]:
    """Une application par persona : c'est ce qui donne à chaque moitié du run
    son propre `run_label` dans le grand livre."""
    app = create_app()
    app.state.llm = LabelledProvider(provider, f"{label}-{persona}")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        return list(
            await asyncio.gather(*(_ask_one(client, semaphore, q, persona) for q in questions))
        )


async def run(
    *,
    model: str | None = None,
    personas: Sequence[str] = (Persona.pokedex, Persona.factual),
    limit: int | None = None,
    label: str | None = None,
    questions_path: Path = QUESTIONS_PATH,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    questions = load_questions(questions_path)[:limit]
    provider = provider or get_provider(model)
    started = datetime.now(UTC)
    label = label or f"{started:%Y%m%d-%H%M%S}-{provider.model}"

    results: list[dict[str, Any]] = []
    for persona in personas:
        results += await _run_persona(provider, questions, persona, label)

    payload = {
        "run": {
            "label": label,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "provider": provider.name,
            "model": provider.model,
            "personas": list(personas),
            "questions_file": str(questions_path),
            "questions_sha256": questions_digest(questions_path),
            "question_count": len(questions),
        },
        "results": results,
    }

    return payload


def _slug(label: str) -> str:
    """Le nom de modèle d'une passerelle porte un « / » — `openai/gpt-oss-120b`.
    Tel quel dans un nom de fichier, il viserait un sous-dossier inexistant.
    L'étiquette d'origine reste intacte dans l'artefact et dans le grand livre.
    """
    return label.replace("/", "-")


def save(payload: dict[str, Any], runs_dir: Path = RUNS_DIR) -> Path:
    """Écrit l'artefact. Sync et hors de `run()` : écrire sur disque depuis une
    coroutine bloquerait la boucle, et ruff a raison de le refuser."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / f"{_slug(payload['run']['label'])}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def execute(**kwargs: Any) -> tuple[dict[str, Any], Path]:
    """Point d'entrée synchrone : exécute le run et persiste son artefact."""
    runs_dir = kwargs.pop("runs_dir", RUNS_DIR)
    payload = asyncio.run(run(**kwargs))
    return payload, save(payload, runs_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Évaluation de la ligne de base.")
    parser.add_argument("--model", default=None, help="surcharge OPENAI_MODEL")
    parser.add_argument(
        "--persona",
        default="both",
        choices=["pokedex", "factual", "both"],
        help="persona à évaluer (défaut : les deux, pour mesurer l'écart)",
    )
    parser.add_argument("--limit", type=int, default=None, help="n premières questions")
    parser.add_argument("--label", default=None, help="nom du run (défaut : horodatage-modèle)")
    args = parser.parse_args()

    personas = (
        [Persona.pokedex, Persona.factual] if args.persona == "both" else [Persona(args.persona)]
    )
    payload, out = execute(model=args.model, personas=personas, limit=args.limit, label=args.label)
    print(render_markdown(payload))
    print(f"\nArtefact : {out}")


if __name__ == "__main__":
    main()
