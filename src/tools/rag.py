"""Recherche dans le corpus de Pokédex, avec citations obligatoires.

Le pendant de `src/tools/sql.py`, et la même exigence : **une réponse doit
pouvoir être revérifiée**. Là où l'outil SQL cite la requête exécutée, celui-ci
cite `pokedex:<espèce>/<jeu>` — une paire qui retrouve l'entrée exacte en une
requête.

**Le point qui compte est le plancher de similarité.** Une recherche vectorielle
rend *toujours* ses k plus proches voisins : interrogée sur la capitale de la
France, elle rendra les cinq entrées de Pokédex les moins éloignées, et un
modèle à qui on les tend finira par en tirer quelque chose. Savoir ne rien
renvoyer est la moitié du travail d'un outil de recherche.

Comme l'outil SQL, celui-ci lit par le rôle en lecture seule.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import LoreChunk, Species
from src.db.session import get_ro_engine
from src.ingest.lore import MODEL_NAME

log = logging.getLogger("tools.rag")

DEFAULT_K = 5

# Quand l'espèce est connue, on ne cherche plus : on lit ses entrées. Une espèce
# en a 4 à 12, de deux ou trois phrases ; en rendre 8 tient largement dans la
# fenêtre et garantit que la bonne y est. Le classement par distance ne sert
# plus qu'à mettre la plus plausible en tête.
SPECIES_K = 8

# Distance cosinus, entre 0 (identique) et 2 (opposé).
#
# Réglé sur 24 requêtes **hors du jeu d'évaluation** — 12 formulations Pokémon
# quelconques, 12 questions étrangères au domaine. Le calibrer sur les questions
# notées reviendrait à s'entraîner sur sa propre copie.
#
# Mesuré deux fois, sur les deux formes de requête que l'outil reçoit :
#
#   formulations libres      domaine 0,157–0,367   hors domaine 0,363–0,730
#   affirmations « Pokédex » domaine 0,135–0,377   hors domaine 0,349–0,804
#
# À 0,40, dans les deux cas : tout le domaine conservé, 1 requête étrangère sur
# 12 (puis 1 sur 8) laissée passer. Les deux
# distributions **se chevauchent d'un cheveu** — une requête étrangère tombe à
# 0,363, sous le maximum du domaine. Aucun seuil ne les sépare parfaitement, et
# prétendre le contraire serait faux. On préfère laisser passer une requête de
# trop plutôt que d'amputer le rappel : en aval, l'invite demande au modèle de
# dire qu'il ne sait pas quand les passages ne répondent pas à la question.
MAX_DISTANCE = 0.40


@dataclass(frozen=True)
class LoreHit:
    species_fr: str
    species_en: str
    version: str
    text: str
    distance: float

    @property
    def citation(self) -> str:
        """`pokedex:umbreon/black` — l'espèce en anglais, comme la base la nomme,
        pour que la citation soit une clé et non une étiquette."""
        return f"pokedex:{self.species_en}/{self.version}"


@lru_cache
def _model():
    """Chargé une fois par processus : 220 Mo d'ONNX, à ne pas payer par requête.

    Import local pour que l'API ne charge onnxruntime que si quelqu'un cherche
    réellement dans le corpus.
    """
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=MODEL_NAME, cache_dir=str(get_settings().fastembed_cache_dir))


def embed_query(query: str) -> list[float]:
    return next(iter(_model().embed([query]))).tolist()


def _named(nom: str):
    """« Téraclope » ou « dusclops » : le routeur rend ce que la question écrit.

    Égalité insensible à la casse plutôt qu'un `ILIKE` : la valeur vient du
    modèle, et `%` y serait un joker. Un nom d'espèce ne doit pas pouvoir
    ramener la moitié du corpus.
    """
    valeur = nom.strip().lower()
    return or_(func.lower(Species.name_fr) == valeur, func.lower(Species.name) == valeur)


def _rows(session: Session, distance, condition, limite: int):
    stmt = (
        select(Species.name_fr, Species.name, LoreChunk.version, LoreChunk.text, distance)
        .join(Species, Species.id == LoreChunk.species_id)
        .order_by(distance)
        .limit(limite)
    )
    if condition is not None:
        stmt = stmt.where(condition)
    return session.execute(stmt).all()


def search(
    query: str,
    *,
    k: int = DEFAULT_K,
    max_distance: float = MAX_DISTANCE,
    species: str | None = None,
) -> list[LoreHit]:
    """Les entrées de l'espèce nommée, ou les plus proches, ou rien.

    **Deux régimes, et c'est le constat du jalon 4 qui les sépare.** Quand la
    question nomme l'espèce, chercher est le mauvais outil : la réponse est dans
    ses entrées, et une similarité calculée sur une requête que le routeur a dû
    inventer part chercher ailleurs. On filtre, et le plancher de distance ne
    s'applique pas — le filtre *est* la garantie de pertinence.

    Quand elle ne la nomme pas, la similarité redevient le bon outil, avec son
    plancher : c'est lui qui permet de répondre « je ne sais pas » plutôt que de
    tendre au modèle cinq entrées sans rapport.

    Un nom d'espèce non résolu — accent manquant, forme inattendue — **replie
    sur la similarité** plutôt que sur le vide : une orthographe ratée ne doit
    pas coûter la question.
    """
    vecteur = embed_query(query)
    distance = LoreChunk.embedding.cosine_distance(vecteur)
    with Session(get_ro_engine()) as session:
        if species:
            lignes = _rows(session, distance, _named(species), SPECIES_K)
            if lignes:
                return keep_close_enough(lignes, math.inf)
            log.info("espèce « %s » non résolue : repli sur la similarité", species)
        lignes = _rows(session, distance, None, k)
    return keep_close_enough(lignes, max_distance)


def keep_close_enough(lignes: Sequence[tuple], max_distance: float) -> list[LoreHit]:
    """Filtrage et mise en forme, séparés de l'accès à la base.

    Sorti de `search()` pour être testable sans base, sans modèle et sans
    réseau : c'est le seuil qui porte le comportement intéressant, pas la
    requête SQL qui l'entoure.
    """
    return [
        LoreHit(
            species_fr=nom_fr or nom_en,
            species_en=nom_en,
            version=version,
            text=texte,
            distance=float(d),
        )
        for nom_fr, nom_en, version, texte, d in lignes
        if d <= max_distance
    ]
