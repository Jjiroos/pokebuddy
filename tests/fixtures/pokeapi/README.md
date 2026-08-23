# Fixtures PokéAPI

Réponses réelles de `https://pokeapi.co/api/v2`, réduites aux champs que
`src/ingest/pokeapi.py` lit effectivement. Les tableaux `moves`, `sprites` et
`flavor_text_entries` ont été retirés : ils représentaient 95 % du volume et
aucun code ne les touche.

| Fichier | Pourquoi ce choix |
|---|---|
| `pokemon_charizard.json` | Deux types, `game_indices` renseigné (46 entrées) |
| `pokemon_raichu_alola.json` | **Forme régionale** : `is_default: false`, id 10100, espèce `raichu` — une des cinq catégories « pièges » de l'évaluation du jalon 2 |
| `species_charizard.json` | Évolution depuis `charmeleon` |
| `species_raichu.json` | Espèce partagée par Raichu et Raichu d'Alola, dex national 26 |

Rafraîchir : `curl -s https://pokeapi.co/api/v2/pokemon/6` puis appliquer la
même réduction de champs.
