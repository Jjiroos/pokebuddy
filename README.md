# Pokébuddy

Un agent qui répond à des questions sur les Pokémon en interrogeant une base de
données et un corpus documentaire, puis en citant ses sources — plutôt qu'en
répondant de mémoire.

**Le sujet réel n'est pas le Pokédex : c'est de mesurer de combien un agent
outillé bat un LLM nu sur des faits vérifiables.** Le dépôt est construit autour
de cette mesure, et l'historique des runs d'évaluation en est le livrable.

> **État : jalon 1 sur 5 terminé.** Le socle répond, sans aucun outil — c'est la
> ligne de base délibérée, et elle se trompe. Les chiffres arrivent au jalon 2.

---

## Démonstration

*(Capture à venir au jalon 5, avec le front.)*

## Évaluation

| Configuration | Exactitude | Latence p95 | Coût / requête |
|---|---|---|---|
| LLM nu, sans outil | *jalon 2* | | |
| + SQL et RAG | *jalon 3* | | |
| Agent complet | *jalon 4* | | |

Le tableau est vide parce que les 40 questions d'évaluation s'écrivent au
jalon 2, **avant** toute amélioration. Les écrire après reviendrait à optimiser
vers son intuition plutôt que vers un résultat, et à publier une progression
flatteuse. Un tableau vide est plus honnête qu'un tableau inventé.

---

## Architecture

```
                    ┌─────────────┐
   question ───────▶│   Agent     │  (LangGraph — jalon 4)
                    │  (routeur)  │
                    └──────┬──────┘
                           │  choisit un ou plusieurs outils
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌───────────┐     ┌────────────┐     ┌────────────┐
  │ SQL Tool  │     │ RAG Tool   │     │ Calc Tool  │
  │ Postgres  │     │ pgvector   │     │ (dégâts,   │
  │ (faits)   │     │ (lore)     │     │  ratios)   │
  └───────────┘     └────────────┘     └────────────┘
        └──────────────────┼──────────────────┘
                           ▼
                    ┌─────────────┐
                    │  Réponse    │  + citations + trace
                    └─────────────┘
```

**Ce qui existe au jalon 1** : l'API, la couche LLM instrumentée, la base de
faits. Le routeur et les trois outils sont vides — `/ask` appelle le modèle
directement et ne cite rien.

**La décision structurante** : les questions d'agrégation (« quels Pokémon Eau
dépassent 100 en vitesse ? ») partiront en SQL, pas en RAG. Un index vectoriel
est mauvais en comptage et en filtrage numérique. Le lore partira en RAG.
L'arbitrage du routeur est le travail d'ingénierie du projet.

---

## Démarrage

```bash
cp .env.example .env        # y coller une vraie clé OpenAI
make setup                  # venv, dépendances, hooks git
make up                     # PostgreSQL + pgvector, API
make migrate
make ingest                 # ~1350 pokémon, une vingtaine de secondes
make test
```

```bash
curl -s localhost:8000/health | jq
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"Quelles sont les stats de base de Dracaufeu ?"}' | jq
```

Documentation interactive sur <http://localhost:8000/docs>.

| Commande | Effet |
|---|---|
| `make up` / `make down` | démarre / arrête la pile |
| `make ingest LIMIT=50` | ingestion partielle, pour itérer vite |
| `make psql` | console SQL sur la base |
| `make clean` | repart de zéro, données comprises |

---

## Décisions techniques

### `temperature=0` n'existe plus, et c'est intéressant

La recette habituelle pour rendre une évaluation reproductible est de fixer la
température à zéro. **La famille GPT-5 la refuse** : l'API répond
`400 — only the default (1) value is supported`. Les modèles de raisonnement
remplacent le réglage d'échantillonnage par `reasoning.effort` et
`text.verbosity`.

La reproductibilité vient donc d'ailleurs :

* **le cache disque** — clé = empreinte du modèle, des messages, des paramètres
  et du schéma de sortie. Relancer une évaluation ne coûte rien et redonne
  exactement les mêmes réponses ;
* **l'épinglage de `reasoning.effort`** par configuration, avec l'effort inclus
  dans la clé de cache : changer de réglage invalide les réponses au lieu de
  rejouer les anciennes en silence.

Les familles antérieures acceptent encore `temperature`, qui leur est envoyée.
Un seul fichier connaît cette divergence (`src/llm/openai_provider.py`), et un
test vérifie au niveau du fil ce qui part pour chaque famille.

### Le coût voyage avec la réponse

`LLMResponse` porte les tokens, le coût estimé et la latence. Ce n'est pas de
la télémétrie posée à côté : c'est dans le type de retour, donc impossible à
oublier au moment de remplir le tableau d'évaluation.

Un modèle absent de la table de tarifs **lève une exception** au lieu de
renvoyer un coût nul. Le coût par requête est une colonne publiée : un zéro
silencieux serait un chiffre faux dans ce README.

Le fichier SQLite tient aussi le journal de tous les appels
(`llm_calls`, avec `cache_hit`). La somme de sa colonne de coût correspond à la
facture réelle, pas à une estimation — un appel servi par le cache est
enregistré à zéro.

### Le schéma est conçu pour un générateur de SQL

Les statistiques sont en **colonnes larges** (`hp`, `attack`, …, `speed`) et non
dans une table clé/valeur. « Quels Pokémon Eau dépassent 100 en vitesse ? »
devient un `WHERE` évident ; un schéma long obligerait le text-to-SQL du jalon 3
à pivoter, ce qu'un générateur rate justement souvent. On optimise pour le
futur lecteur automatique, pas pour la beauté de la 3NF.

Les **formes régionales** — l'un des pièges prévus de l'évaluation — sont
modélisées nativement par le couple `pokemon` / `species` : Raichu d'Alola est
une ligne distincte, rattachée à l'espèce Raichu, partageant son numéro de
Pokédex, avec ses propres types.

### Le cache d'ingestion est une obligation, pas une optimisation

La politique d'usage de PokéAPI n'impose aucune limite de débit mais **exige** le
cache local des ressources, sous peine de bannissement d'IP. Effet de bord
utile : les tests d'ingestion tournent hors ligne et une réingestion complète
est instantanée.

### Changer de fournisseur

`LLMProvider` est un `Protocol`, sélectionné par `LLM_PROVIDER`. Rien en dehors
de `src/llm/` ne connaît OpenAI. Mistral et Ollama arrivent au jalon 5, pour le
comparatif d'arbitrage.

### Limites connues, dans la source

`game_indices` de PokéAPI, qui alimente les apparitions par jeu, est renseigné
pour les générations I à VII et **vide au-delà**. PokéAPI expose par ailleurs des
formes non canoniques (`raichu-mega-x` n'existe pas dans les jeux). Les deux
sont des propriétés de la source, pas de l'ingestion — à garder en tête en
écrivant les questions d'évaluation.

---

## Développement

Le dépôt vit sur un montage Windows (`/mnt/z`) sous WSL2, où les entrées/sorties
sont lentes. Trois conséquences, toutes automatisées :

* le venv est créé sous `$HOME` (`UV_PROJECT_ENVIRONMENT`, posé par le
  `Makefile` — **passer par `make`**, un `uv run` nu recrée un venv lent dans le
  dépôt) ;
* les données PostgreSQL et les caches sont dans des volumes Docker nommés,
  jamais des bind mounts ;
* `WATCHFILES_FORCE_POLLING` est activé, sans quoi le rechargement à chaud
  d'uvicorn est silencieusement mort à travers `/mnt/`.

Le port hôte de PostgreSQL est `5433` par défaut (`POSTGRES_HOST_PORT`), 5432
étant souvent déjà pris par une installation locale.

`gitleaks` et `ruff` tournent en pre-commit. `.env` a été ignoré par git dans un
commit antérieur à l'écriture de toute clé.

---

## Feuille de route

| Jalon | Contenu | État |
|---|---|---|
| 1 | Socle : API, couche LLM instrumentée, base de faits | **terminé** |
| 2 | 40 questions d'évaluation, harnais, ligne de base chiffrée | à venir |
| 3 | Outil SQL contraint, RAG avec citations obligatoires | |
| 4 | Agent LangGraph, cas dégradés, traces Langfuse | |
| 5 | CI, image publiée, front React, comparatif multi-fournisseurs | |

---

## Sources & licences

| Source | Usage | Licence |
|---|---|---|
| [PokéAPI](https://pokeapi.co) | stats, espèces, types, évolutions, jeux | données libres ; politique d'usage imposant le cache local |
| [Pokémon TCG API](https://pokemontcg.io) | cartes et illustrateurs *(à partir du jalon 2)* | usage non commercial |
| [Bulbapedia](https://bulbapedia.bulbagarden.net) | lore et contexte *(jalon 3)* | CC BY-NC-SA — attribution obligatoire |

**Pokémon est une marque de Nintendo / Creatures / GAME FREAK.** Ce projet est
un travail personnel non commercial, sans affiliation. Aucune image de carte ni
ressource graphique sous droits n'est redistribuée dans ce dépôt : seules des
données factuelles issues d'API publiques y sont ingérées, et le cache local
reste hors du dépôt.
