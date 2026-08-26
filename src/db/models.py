"""Modèle relationnel des faits Pokémon.

**Décision structurante : les statistiques sont en colonnes larges**, pas dans
une table clé/valeur. « Quels Pokémon Eau ont plus de 100 en vitesse ? » devient
un simple WHERE. Un schéma long obligerait le text-to-SQL du jalon 3 à pivoter,
ce qu'un générateur de SQL rate justement souvent. On conçoit le schéma pour son
futur lecteur automatique, pas pour la beauté de la 3NF.
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Species(Base):
    """Espèce. Une espèce peut avoir plusieurs formes (voir Pokemon)."""

    __tablename__ = "species"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # PokéAPI est anglophone (`gyarados`) et les questions sont en français
    # (« Léviator »). Sans cette colonne, le générateur de SQL du jalon 3 devrait
    # traduire de mémoire — exactement ce qu'on cherche à ne plus lui demander.
    name_fr: Mapped[str | None] = mapped_column(String(64), index=True)
    generation: Mapped[int | None] = mapped_column(Integer, index=True)
    # Auto-référence : suffit à couvrir les évolutions du jalon 1 sans table de
    # chaînes. Les évolutions ramifiées (Évoli) restent lisibles en remontant
    # les parents.
    evolves_from_species_id: Mapped[int | None] = mapped_column(
        ForeignKey("species.id", ondelete="SET NULL"), index=True
    )

    evolves_from: Mapped[Species | None] = relationship(remote_side=[id])
    pokemon: Mapped[list[Pokemon]] = relationship(back_populates="species")


class Type(Base):
    __tablename__ = "types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # « Eau » et non « water ». Le nom français appartient à la base, pas à une
    # invite : une table de correspondance dans le prompt serait une donnée
    # dupliquée, non testable, et invisible à qui lit le schéma.
    name_fr: Mapped[str | None] = mapped_column(String(32), index=True)


class Version(Base):
    """Un jeu (Rouge, Écarlate…)."""

    __tablename__ = "versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class Pokemon(Base):
    """Une forme jouable. Raichu d'Alola est une ligne distincte de Raichu,
    partageant la même espèce et marquée is_default = false.

    Les formes régionales sont l'une des cinq catégories « pièges » de
    l'évaluation du jalon 2 : cette modélisation est ce qui permettra d'y
    répondre juste.
    """

    __tablename__ = "pokemon"

    id: Mapped[int] = mapped_column(primary_key=True)
    species_id: Mapped[int] = mapped_column(
        ForeignKey("species.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    national_dex_number: Mapped[int | None] = mapped_column(Integer, index=True)
    is_default: Mapped[bool] = mapped_column(default=True, index=True)

    height_dm: Mapped[int | None]
    weight_hg: Mapped[int | None]

    hp: Mapped[int | None] = mapped_column(Integer, index=True)
    attack: Mapped[int | None] = mapped_column(Integer, index=True)
    defense: Mapped[int | None] = mapped_column(Integer, index=True)
    special_attack: Mapped[int | None] = mapped_column(Integer, index=True)
    special_defense: Mapped[int | None] = mapped_column(Integer, index=True)
    speed: Mapped[int | None] = mapped_column(Integer, index=True)

    species: Mapped[Species] = relationship(back_populates="pokemon")
    types: Mapped[list[PokemonType]] = relationship(
        back_populates="pokemon", cascade="all, delete-orphan"
    )


class PokemonType(Base):
    __tablename__ = "pokemon_types"
    __table_args__ = (UniqueConstraint("pokemon_id", "slot", name="uq_pokemon_type_slot"),)

    pokemon_id: Mapped[int] = mapped_column(
        ForeignKey("pokemon.id", ondelete="CASCADE"), primary_key=True
    )
    type_id: Mapped[int] = mapped_column(
        ForeignKey("types.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    slot: Mapped[int]

    pokemon: Mapped[Pokemon] = relationship(back_populates="types")
    type: Mapped[Type] = relationship()


class PokemonGameAppearance(Base):
    """Apparitions par jeu.

    Alimentée par `game_indices` de PokéAPI, qui est renseigné pour les
    générations I à VII mais vide pour les plus récentes. La lacune est donc
    dans la source, pas dans l'ingestion — à garder en tête en écrivant les
    questions d'évaluation du jalon 2.
    """

    __tablename__ = "pokemon_game_appearances"

    pokemon_id: Mapped[int] = mapped_column(
        ForeignKey("pokemon.id", ondelete="CASCADE"), primary_key=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True
    )


class CardSet(Base):
    """Une extension du JCC — « Set de Base », « Voltage Éclatant »…

    Les identifiants sont ceux de TCGdex (`base1`, `swsh4`) : garder la clé de
    la source rend chaque ligne revérifiable en une requête HTTP, ce qui est
    exactement ce que la vérité terrain de l'évaluation demande.
    """

    __tablename__ = "card_sets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(128), index=True)
    name_fr: Mapped[str | None] = mapped_column(String(128), index=True)
    serie: Mapped[str | None] = mapped_column(String(128), index=True)
    # Chaîne ISO plutôt que Date : TCGdex sert parfois des dates partielles, et
    # l'ordre lexicographique d'une date ISO est déjà l'ordre chronologique.
    release_date: Mapped[str | None] = mapped_column(String(16))
    card_count: Mapped[int | None] = mapped_column(Integer)

    cards: Mapped[list[Card]] = relationship(back_populates="card_set")


class Card(Base):
    """Une carte. `illustrator` est la colonne qui justifie cette table.

    L'évaluation du jalon 2 y consacre dix questions, et le modèle nu y plafonne
    à 20 % : c'est la connaissance la plus absente de sa mémoire, donc celle qui
    se gagne le plus en allant la chercher en base.
    """

    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("card_sets.id", ondelete="CASCADE"), index=True)
    # Chaîne, pas entier : TCGdex numérote certaines cartes « ! » ou « ? »
    # (`exu-!` existe). Un Integer ferait tomber l'ingestion sur ces lignes.
    local_id: Mapped[str] = mapped_column(String(16), index=True)
    name_en: Mapped[str] = mapped_column(String(128), index=True)
    name_fr: Mapped[str | None] = mapped_column(String(128), index=True)
    illustrator: Mapped[str | None] = mapped_column(String(128), index=True)
    rarity: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(32))

    card_set: Mapped[CardSet] = relationship(back_populates="cards")


# Dimension du modèle de plongement (voir src/tools/rag.py). En dur ici parce
# qu'une colonne `vector(n)` ne peut pas la déduire : changer de modèle impose
# une migration, ce qui est une bonne chose — cela oblige à réindexer.
EMBEDDING_DIM = 384


class LoreChunk(Base):
    """Une entrée de Pokédex, telle quelle. **C'est le document.**

    Aucun découpage : ces textes font deux ou trois phrases, le grain naturel du
    corpus. Découper ce qui est déjà court ajouterait un paramètre à régler sans
    rien améliorer, et un paramètre non réglé est une dette.

    C'est le seul contenu du projet que la base relationnelle ne sait pas
    représenter : « quand il s'expose à la lumière de la lune, ses anneaux
    brillent » n'est ni une statistique, ni un type, ni une évolution.
    """

    __tablename__ = "lore_chunks"
    __table_args__ = (UniqueConstraint("species_id", "text_hash", name="uq_lore_species_text"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    species_id: Mapped[int] = mapped_column(
        ForeignKey("species.id", ondelete="CASCADE"), index=True
    )
    # Le jeu d'origine. Il part dans la citation : `pokedex:noctali/black` se
    # revérifie en une commande, comme le SQL de l'outil relationnel.
    version: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    # PokéAPI répète la même entrée à l'identique entre versions d'un même jeu.
    # L'empreinte du texte est la clé de déduplication.
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    species: Mapped[Species] = relationship()
