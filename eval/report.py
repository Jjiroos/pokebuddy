"""Agrégation d'un artefact de run en tableau markdown.

Le rapport est le livrable : c'est lui qui se colle dans le README. Il vit à
part du runner pour qu'on puisse le régénérer d'un run passé sans repayer les
appels — et pour que les deux runs du jalon 3 se comparent avec le même code.

    python -m eval.report eval/runs/<fichier>.json
    python -m eval.report <nouveau>.json --contre <baseline>.json
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


class QuestionsDiverge(ValueError):
    """Deux runs sur des questions différentes ne se comparent pas.

    L'empreinte du jeu de questions voyage dans chaque artefact précisément pour
    rendre cette erreur impossible à commettre en silence : publier un « avant /
    après » dont les deux moitiés n'ont pas répondu aux mêmes questions serait
    la façon la plus simple de fabriquer une progression flatteuse.
    """


def _points(avant: tuple[int, int], apres: tuple[int, int]) -> str:
    if not avant[1] or not apres[1]:
        return "—"
    ecart = 100 * apres[0] / apres[1] - 100 * avant[0] / avant[1]
    signe = "+" if ecart >= 0 else "−"
    return f"{signe}{_fr(abs(ecart))} pts"


def render_comparison(
    apres: dict[str, Any],
    avant: dict[str, Any],
    *,
    nom_avant: str = "Avant",
    nom_apres: str = "Après",
) -> str:
    empreintes = (avant["run"]["questions_sha256"], apres["run"]["questions_sha256"])
    if empreintes[0] != empreintes[1]:
        raise QuestionsDiverge(
            f"Les deux runs n'ont pas répondu aux mêmes questions : {empreintes[0]} "
            f"contre {empreintes[1]}. La comparaison n'aurait aucun sens."
        )

    a, b = avant["results"], apres["results"]
    sa, sb = summarize(avant), summarize(apres)
    lines: list[str] = []

    lines.append(f"## Avant / après — `{apres['run']['model']}`")
    lines.append("")
    lines.append(
        f"Runs `{avant['run']['label']}` et `{apres['run']['label']}`, "
        f"même jeu de questions `{empreintes[0]}`."
    )
    lines.append("")

    lines.append(f"| Catégorie | {nom_avant} | {nom_apres} | Écart |")
    lines.append("|---|---|---|---|")
    for category, label in CATEGORY_LABELS.items():
        ra = [r for r in a if r["category"] == category]
        rb = [r for r in b if r["category"] == category]
        if not ra and not rb:
            continue
        lines.append(
            f"| {label} | {_cell(ra)} | {_cell(rb)} | {_points(_accuracy(ra), _accuracy(rb))} |"
        )
    lines.append(
        f"| **Ensemble** | **{_cell(a)}** | **{_cell(b)}** | "
        f"**{_points(_accuracy(a), _accuracy(b))}** |"
    )
    lines.append("")

    lines.append(f"| Mesure | {nom_avant} | {nom_apres} |")
    lines.append("|---|---|---|")
    cout_a = sa["cost_usd"] / len(a) if a else 0.0
    cout_b = sb["cost_usd"] / len(b) if b else 0.0
    lines.append(f"| Coût par question | {cout_a:.5f} $ | {cout_b:.5f} $ |")
    for nom, key in (("Latence p50", "latency_p50"), ("Latence p95", "latency_p95")):
        va, vb = sa[key], sb[key]
        fmt = lambda v: "—" if v is None else f"{v:.0f} ms"  # noqa: E731
        lines.append(f"| {nom} | {fmt(va)} | {fmt(vb)} |")
    for nom, key in (
        ("Confiance quand juste", "confidence_when_right"),
        ("Confiance quand faux", "confidence_when_wrong"),
    ):
        va, vb = sa[key], sb[key]
        fmt2 = lambda v: "—" if v is None else _fr(v, 2)  # noqa: E731
        lines.append(f"| {nom} | {fmt2(va)} | {fmt2(vb)} |")

    # Un run de référence est souvent celui qui a rejoué les questions perdues :
    # il est alors massivement servi par le cache, et son coût comme sa latence
    # ne mesurent plus rien. L'exactitude, elle, reste exacte — les réponses
    # rejouées sont identiques au mot près. Mieux vaut que le rapport le dise
    # que de laisser coller un « coût par question » de zéro dans un README.
    for nom, resume, total in ((nom_avant, sa, len(a)), (nom_apres, sb, len(b))):
        if total and resume["cache_hits"] / total > 0.5:
            lines.append("")
            lines.append(
                f"> ⚠ **{nom}** est servi à {resume['cache_hits']}/{total} par le cache : "
                "son coût et sa latence ne mesurent rien. Les relever sur le premier "
                "run de la série, seul à avoir réellement appelé le fournisseur. "
                "L'exactitude, elle, est inchangée — le cache rend la réponse au mot près."
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rapport markdown d'un run d'évaluation.")
    parser.add_argument("run_file", type=Path, help="eval/runs/<fichier>.json")
    parser.add_argument(
        "--contre", type=Path, default=None, help="artefact de référence, pour un avant/après"
    )
    parser.add_argument("--avant", default="Avant", help="libellé de la colonne de référence")
    parser.add_argument("--apres", default="Après", help="libellé de la colonne du nouveau run")
    args = parser.parse_args()

    payload = json.loads(args.run_file.read_text(encoding="utf-8"))
    if args.contre is None:
        print(render_markdown(payload))
        return
    baseline = json.loads(args.contre.read_text(encoding="utf-8"))
    print(render_comparison(payload, baseline, nom_avant=args.avant, nom_apres=args.apres))


if __name__ == "__main__":
    main()
