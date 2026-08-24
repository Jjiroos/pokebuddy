"""Notation déterministe des réponses.

Aucun appel LLM, aucun réseau : un juge est un second modèle, dont l'erreur
devrait elle-même être mesurée, et dont le verdict ne serait pas reproductible
(la famille GPT-5 refuse `temperature=0`). On préfère payer le prix en amont —
n'écrire que des questions à réponse fermée — plutôt qu'en aval.

La contrepartie est assumée : le grader est *littéral*. Il vérifie la présence
de faits, pas la justesse d'un raisonnement, et il est légèrement clément sur
les comptages (« environ 5 à 6 » valide un 6 attendu). Les deux limites sont
documentées dans le README plutôt que corrigées par de l'astuce.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Élision française : « d'Alola » doit rejoindre « alola », sans quoi un
# apostrophe suffirait à faire manquer un nom présent dans la réponse.
_ELISION = re.compile(r"\b[a-z]['\u2019]")
_DIGITS = re.compile(r"\d+")

KINDS = ("numbers_all", "number", "names")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str


def normalize(text: str) -> str:
    """Ramène un texte à une suite de mots comparable, encadrée d'espaces.

    « Raichu d'Alola », « raichu-alola » et « RAICHU D'ALOLA » convergent.
    Les espaces de bord font office de frontières de mot : un simple test de
    sous-chaîne suffit ensuite, et « rat » ne valide plus sur « Rattatac ».
    """
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    without_elision = _ELISION.sub("", without_accents)
    return f" {_NON_ALNUM.sub(' ', without_elision).strip()} "


def _contains(haystack: str, needle: str) -> bool:
    """`haystack` est déjà normalisé et encadré d'espaces."""
    return normalize(needle) in haystack


def _aliases(entry: Any) -> list[str]:
    """Une entrée de `all_of` / `none_of` est un nom, ou une liste de graphies.

    Le modèle répond en français, la base est en anglais : « barraskewda » et
    « Hastacuda » désignent le même Pokémon et doivent valider indifféremment.
    """
    return [str(entry)] if isinstance(entry, str) else [str(a) for a in entry]


def _numbers_in(haystack: str) -> list[int]:
    return [int(m) for m in _DIGITS.findall(haystack)]


def grade(answer: str, check: dict[str, Any]) -> Verdict:
    """Confronte une réponse en prose à la règle de correspondance d'une question."""
    kind = check.get("kind")
    haystack = normalize(answer)

    match kind:
        case "numbers_all":
            expected = list(dict.fromkeys(check["values"]))
            missing = [v for v in expected if not _contains(haystack, str(v))]
            if missing:
                return Verdict(False, f"valeurs absentes : {missing}")
            return Verdict(True, "toutes les valeurs attendues sont présentes")

        case "number":
            value = int(check["value"])
            tolerance = int(check.get("tolerance", 0))
            found = _numbers_in(haystack)
            if any(abs(n - value) <= tolerance for n in found):
                return Verdict(True, f"{value} trouvé")
            return Verdict(False, f"{value} absent (nombres cités : {found or 'aucun'})")

        case "names":
            missing = [
                _aliases(e)[0]
                for e in check.get("all_of", [])
                if not any(_contains(haystack, a) for a in _aliases(e))
            ]
            forbidden = [
                a for e in check.get("none_of", []) for a in _aliases(e) if _contains(haystack, a)
            ]
            if missing and forbidden:
                return Verdict(False, f"manque {missing} ; cite à tort {forbidden}")
            if missing:
                return Verdict(False, f"manque {missing}")
            if forbidden:
                # Le détecteur d'hallucination. Une réponse qui cite trois noms
                # justes et deux inventés est fausse, pas à moitié juste.
                return Verdict(False, f"cite à tort {forbidden}")
            return Verdict(True, "tous les noms attendus, aucun nom interdit")

        case _:
            raise ValueError(f"`kind` inconnu : {kind!r}. Attendu l'un de {KINDS}.")
