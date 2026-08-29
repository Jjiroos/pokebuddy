"""Description du schéma envoyée au générateur de SQL.

**Dérivée de `Base.metadata`, jamais recopiée à la main.** Un schéma décrit en
dur diverge du réel dès la migration suivante, et le modèle se met alors à
écrire du SQL contre des colonnes qui n'existent plus — une panne silencieuse,
qui se lit comme une baisse d'exactitude sans cause apparente.

Ce qui reste écrit à la main, ce sont les **notes** : elles portent une
connaissance du domaine qu'aucun métamodèle ne contient, et chacune correspond à
une catégorie de questions que l'évaluation pose réellement.
"""

from __future__ import annotations

from sqlalchemy import Column, Table

from src.db.models import Base
from src.tools.sql import MAX_ROWS

# Chaque note existe parce qu'une question d'évaluation échouerait sans elle.
NOTES = f"""\
Notes indispensables :

- **Les colonnes `name` des tables PokéAPI contiennent l'identifiant anglais**,
  en minuscules et avec des traits d'union : `gyarados`, `wooper`, `water`,
  `flying`. Le français vit dans les colonnes `name_fr` — `species.name_fr`
  (`Léviator`), `types.name_fr` (`Eau`, `Vol`), `card_sets.name_fr`,
  `cards.name_fr`. Pour une question posée en français, filtrer sur `name_fr` —
  ne jamais traduire un nom de mémoire pour le comparer à `name`.
- **`pokemon` n'a pas de colonne `name_fr`.** Elle n'existe pas : le nom
  français d'une forme s'obtient en joignant `species` et en lisant
  `species.name_fr`. Écrire `pokemon.name_fr` fait échouer la requête.
- `pokemon` contient une ligne par **forme**, pas par espèce. Les
  méga-évolutions, formes régionales et Gigamax sont des lignes de plein droit,
  avec `is_default = false`. « Les formes standard » veut dire
  `is_default = true`, et l'oublier fausse tout comptage. **Mais une question
  qui nomme une région — « de Galar », « d'Alola », « de Paldea » — demande
  exactement l'inverse** : la forme régionale porte `is_default = false`, et y
  ajouter `is_default = true` rend la forme standard, donc la mauvaise réponse.
- Les formes régionales suivent la convention
  `<identifiant anglais de l'espèce>-<région>` dans `pokemon.name` :
  `raichu-alola`, `wooper-paldea`, `corsola-galar`. Le préfixe est donc
  `species.name`, jamais `species.name_fr`. Pour trouver « Axoloto de Paldea »,
  joindre `species` sur `name_fr = 'Axoloto'` puis filtrer
  `pokemon.name LIKE '%-paldea'` — et surtout pas `pokemon.name =
  'axoloto-paldea'`, qui n'existe pas.
- Les statistiques de base sont des colonnes de `pokemon`
  (`hp`, `attack`, `defense`, `special_attack`, `special_defense`, `speed`),
  pas des lignes d'une table clé/valeur.
- Les cartes du JCC sont dans `cards`, leur extension dans `card_sets`.
  Les questions nomment l'extension en français (`card_sets.name_fr` :
  « Set de Base », « Voltage Éclatant ») et la carte par son numéro
  (`cards.local_id`, qui est du **texte**, pas un entier). **Une extension et un
  numéro identifient une carte à eux seuls** : n'ajoute pas de filtre sur le nom
  de la carte, il ne peut que la faire disparaître.
- **`cards` échappe à la règle des identifiants en minuscules.** Ses noms
  viennent de TCGdex et non de PokéAPI : `cards.name_en` vaut `Lugia`,
  `cards.name_fr` vaut `Dracaufeu`, capitalisés. Ce sont des noms d'affichage,
  bons à renvoyer, mauvais à filtrer.
- **Aucune colonne ne relie `cards` à `species` ni à `pokemon`** : les deux
  sources n'ont pas d'identifiant commun, et la jointure n'existe pas. Une
  question du type « la carte de ce Pokémon » ne se répond que par l'extension
  et le numéro qu'elle donne ; s'ils manquent, mets `sql` à null.
- `pokemon_game_appearances` n'est renseignée que pour les générations I à VII :
  c'est une lacune de la source, pas de l'ingestion.
- La base est en **lecture seule** et `LIMIT {MAX_ROWS}` est imposé d'office.\
"""


def _column(col: Column) -> str:
    parts = [col.name, str(col.type)]
    if col.primary_key:
        parts.append("PK")
    parts += [f"-> {fk.target_fullname}" for fk in col.foreign_keys]
    if not col.nullable:
        parts.append("NOT NULL")
    return " ".join(parts)


def _table(table: Table) -> str:
    # Itérer `table.columns` donne les colonnes elles-mêmes, pas leurs noms.
    columns = ",\n  ".join(_column(col) for col in table.columns)
    return f"{table.name}(\n  {columns}\n)"


def schema_description() -> str:
    tables = "\n\n".join(
        _table(Base.metadata.tables[name]) for name in sorted(Base.metadata.tables)
    )
    return f"Schéma PostgreSQL :\n\n{tables}\n\n{NOTES}"
