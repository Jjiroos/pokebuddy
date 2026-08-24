"""Agrégation d'un artefact de run en tableau markdown.

Le rapport est le livrable : c'est lui qui se colle dans le README. Il vit à
part du runner pour qu'on puisse le régénérer d'un run passé sans repayer les
appels — et pour que les deux runs du jalon 3 se comparent avec le même code.

    python -m eval.report eval/runs/<fichier>.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

CATEGORY_LABELS = {
    "factuel": "Factuel",
    "agregation": "Agrégation",
    "illustrateur": "Illustrateurs",
    "piege": "Pièges",
}


def _fr(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def _percentile(values: list[float], q: float) -> float | None:
    """Rang le plus proche. Sur 40 mesures, interpoler serait de la fausse précision."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _accuracy(results: list[dict[str, Any]]) -> tuple[int, int]:
    return sum(1 for r in results if r["ok"]), len(results)


def _cell(results: list[dict[str, Any]]) -> str:
    ok, total = _accuracy(results)
    if not total:
        return "—"
    return f"{ok}/{total} ({_fr(100 * ok / total)} %)"


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload["results"]
    personas = payload["run"]["personas"]
    # La latence d'un appel servi par le cache ne mesure rien : on la retire.
    billed = [r for r in results if not r["cache_hit"] and r["error"] is None]
    latencies = [float(r["latency_ms"]) for r in billed]

    graded = [r for r in results if r["confidence"] is not None]
    right = [r["confidence"] for r in graded if r["ok"]]
    wrong = [r["confidence"] for r in graded if not r["ok"]]

    ok, total = _accuracy(results)
    return {
        "accuracy": {"ok": ok, "total": total},
        "per_persona": {p: _accuracy([r for r in results if r["persona"] == p]) for p in personas},
        "cost_usd": sum(r["cost_usd"] for r in results),
        "cache_hits": sum(1 for r in results if r["cache_hit"]),
        "errors": sum(1 for r in results if r["error"]),
        "latency_p50": _percentile(latencies, 0.50),
        "latency_p95": _percentile(latencies, 0.95),
        "confidence_when_right": sum(right) / len(right) if right else None,
        "confidence_when_wrong": sum(wrong) / len(wrong) if wrong else None,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    run, results = payload["run"], payload["results"]
    personas = run["personas"]
    s = summarize(payload)
    lines: list[str] = []

    lines.append(f"## Ligne de base — `{run['model']}`")
    lines.append("")
    lines.append(
        f"{run['question_count']} questions × {len(personas)} persona(s) = {len(results)} appels. "
        f"Jeu de questions `{run['questions_sha256']}`, run `{run['label']}`."
    )
    lines.append("")

    lines.append("| Persona | Exactitude |")
    lines.append("|---|---|")
    for persona in personas:
        lines.append(f"| `{persona}` | {_cell([r for r in results if r['persona'] == persona])} |")
    lines.append(f"| **ensemble** | **{_cell(results)}** |")
    lines.append("")

    lines.append("### Par catégorie")
    lines.append("")
    lines.append("| Catégorie | " + " | ".join(f"`{p}`" for p in personas) + " | Ensemble |")
    lines.append("|---" * (len(personas) + 2) + "|")
    for category, label in CATEGORY_LABELS.items():
        rows = [r for r in results if r["category"] == category]
        if not rows:
            continue
        cells = [_cell([r for r in rows if r["persona"] == p]) for p in personas]
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {_cell(rows)} |")
    lines.append("")

    lines.append("### Coût et latence")
    lines.append("")
    lines.append("| Mesure | Valeur |")
    lines.append("|---|---|")
    lines.append(f"| Coût du run | {s['cost_usd']:.4f} $ |")
    per_call = s["cost_usd"] / len(results) if results else 0.0
    lines.append(f"| Coût par question | {per_call:.5f} $ |")
    for name, key in (("p50", "latency_p50"), ("p95", "latency_p95")):
        value = s[key]
        lines.append(f"| Latence {name} | {'—' if value is None else f'{value:.0f} ms'} |")
    lines.append(f"| Appels servis par le cache | {s['cache_hits']}/{len(results)} |")
    if s["errors"]:
        lines.append(f"| Appels en erreur (comptés faux) | {s['errors']} |")
    lines.append("")
    lines.append(
        "*La latence est mesurée sur les seuls appels réellement partis chez le "
        "fournisseur : celle d'un appel servi par le cache ne mesure rien.*"
    )
    lines.append("")

    lines.append("### Calibration")
    lines.append("")
    lines.append("| Le modèle a… | Confiance moyenne qu'il s'attribue |")
    lines.append("|---|---|")
    for label, key in (
        ("répondu juste", "confidence_when_right"),
        ("répondu faux", "confidence_when_wrong"),
    ):
        value = s[key]
        lines.append(f"| {label} | {'—' if value is None else _fr(value, 2)} |")
    lines.append("")
    lines.append(
        "*Un écart faible signifie que le modèle ne sait pas qu'il ne sait pas — "
        "c'est la mesure qui justifie d'aller chercher les faits ailleurs qu'en mémoire.*"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rapport markdown d'un run d'évaluation.")
    parser.add_argument("run_file", type=Path, help="eval/runs/<fichier>.json")
    args = parser.parse_args()
    print(render_markdown(json.loads(args.run_file.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
