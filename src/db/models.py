"""Modèle relationnel des faits Pokémon.

**Décision structurante : les statistiques sont en colonnes larges**, pas dans
une table clé/valeur. « Quels Pokémon Eau ont plus de 100 en vitesse ? » devient
un simple WHERE. Un schéma long obligerait le text-to-SQL du jalon 3 à pivoter,
ce qu'un générateur de SQL rate justement souvent. On conçoit le schéma pour son
futur lecteur automatique, pas pour la beauté de la 3NF.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Species(Base):
    """Espèce. Une espèce peut avoir plusieurs formes (voir Pokemon)."""

    __tablename__ = "species"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
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
