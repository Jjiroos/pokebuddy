# Pokébuddy

Un agent qui répond à des questions sur les Pokémon en interrogeant une base de
données et un corpus documentaire, puis en citant ses sources — plutôt qu'en
répondant de mémoire.

**Le sujet réel n'est pas le Pokédex : c'est de mesurer de combien un agent
outillé bat un LLM nu sur des faits vérifiables.** Le dépôt est construit autour
de cette mesure, et l'historique des runs d'évaluation en est le livrable.

> **État : jalon 2 sur 5 terminé.** La ligne de base est mesurée : **53,8 %**
> d'exactitude sur 40 questions vérifiables, sans aucun outil. C'est le chiffre
> que les jalons suivants doivent battre.

---

## Démonstration

*(Capture à venir au jalon 5, avec le front.)*

## Évaluation

40 questions à réponse fermée, chacune portant sa vérité terrain et la requête
SQL ou l'identifiant de carte qui permet de la revérifier. Elles ont été écrites
et commitées **avant le premier run**, dans un commit qui ne contenait qu'elles :
les écrire après aurait permis de choisir celles qu'on sait déjà cassées.

Notation par grader déterministe, sans juge LLM — un juge est un second modèle,
dont l'erreur devrait elle-même être mesurée.

| Configuration | Exactitude | Latence p50 / p95 | Coût / requête |
|---|---|---|---|
| LLM nu, sans outil | **53,8 %** — 43/80 | 2,0 s / 16,9 s | 0,00011 $ |
| + SQL et RAG | *jalon 3* | | |
| Agent complet | *jalon 4* | | |

### Où le modèle se casse

| Catégorie | Exactitude | Ce que ça dit |
|---|---|---|
| Factuel — stats, types, évolutions | 27/30 — **90,0 %** | la mémoire du modèle couvre bien les faits de base |
| Agrégation — comptages, filtres, tris | 10/20 — **50,0 %** | il ne sait pas parcourir un ensemble. Cible directe de l'outil SQL du jalon 3 |
| Illustrateurs — cartes JCC | 4/20 — **20,0 %** | connaissance absente de sa mémoire. Cible du RAG |
| Pièges — formes régionales, génération 9 | 2/10 — **20,0 %** | là où la mémoire n'est pas absente mais **fausse** |

L'écart entre les personas est négligeable : 52,5 % pour `pokedex`, 55,0 % pour
`factual`. La mise en scène ne dégrade pas l'exactitude à ce jalon.

### Le modèle ne sait pas qu'il ne sait pas

Chaque réponse porte une auto-évaluation de confiance, demandée par le schéma
depuis le jalon 1.

| Le modèle a… | Confiance moyenne qu'il s'attribue |
|---|---|
| répondu juste | 0,96 |
| répondu faux | 0,73 |

L'écart existe, mais il est trop faible pour servir de garde-fou. Le cas le plus
net : interrogé sur les types d'**Axoloto de Paldea** (Poison/Sol), le modèle
répond « Eau » — la forme de Kanto — avec une confiance de **0,97**. Une réponse
fausse et sûre d'elle est pire qu'un aveu d'ignorance, et c'est précisément ce
qui justifie d'aller chercher les faits ailleurs qu'en mémoire.

### Reproductibilité

La famille GPT-5 refuse `temperature=0` ; la garantie repose donc sur le cache
disque. Deux runs consécutifs le vérifient : **78 des 80 appels rejoués au mot
près, à coût nul** (0,0086 $ puis 0,0002 $). Les deux divergences sont les deux
appels que le premier run avait perdus sur un plafond de débit du palier gratuit
— les échecs ne sont délibérément pas mis en cache, donc ils sont rejoués.

Le run publié ci-dessus est le second, complet et sans erreur. Latence et coût
proviennent du premier, seul à avoir réellement appelé le fournisseur. Les deux
artefacts sont dans `eval/runs/` : ce sont les pièces justificatives du tableau.
La latence p95 est gonflée par les temporisations du palier gratuit, pas par le
modèle — le p50 est plus représentatif.

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

## Installation

**Prérequis** : Docker et Docker Compose, `make`, `git`, et
[`uv`](https://docs.astral.sh/uv/) pour ce qui tourne hors conteneur (tests et
évaluation). Une clé d'API est nécessaire à `/ask` et `/extract` ; la base,
l'ingestion et les tests fonctionnent sans.

La configuration par défaut vise **Groq**, dont le palier Developer est gratuit
et sans carte bancaire — de quoi rejouer l'évaluation sans rien dépenser. Créer
une clé sur <https://console.groq.com>, puis la coller dans `.env` :

```ini
OPENAI_API_KEY=gsk_...
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=openai/gpt-oss-120b
```

Pour viser OpenAI, vider `OPENAI_BASE_URL` et remettre un modèle de la table de
`src/llm/pricing.py`.

```bash
git clone https://github.com/Jjiroos/pokebuddy.git
cd pokebuddy
cp .env.example .env         # puis coller une vraie clé dans OPENAI_API_KEY
make setup                   # venv, dépendances, hooks git
make up                      # PostgreSQL + pgvector, API
make migrate                 # crée le schéma
make ingest                  # ~1350 pokémon, une vingtaine de secondes
make test                    # doit passer sans réseau
```

Vérifier que la pile répond :

```bash
curl -s localhost:8000/health | jq
# → {"status":"ok","db":"ok","llm_provider":"api.groq.com","model":"openai/gpt-oss-120b"}

curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"Quelles sont les stats de base de Dracaufeu ?"}' | jq
# → answer non vide, sources [], cost_usd > 0, cache_hit false
# rejouer la même requête : cache_hit true, cost_usd 0, latence effondrée
```

Documentation interactive sur <http://localhost:8000/docs>.

| Commande | Effet |
|---|---|
| `make up` / `make down` | démarre / arrête la pile |
| `make ingest LIMIT=50` | ingestion partielle, pour itérer vite |
| `make eval` | rejoue les 40 questions d'évaluation |
| `make report RUN=eval/runs/<fichier>.json` | régénère le tableau d'un run passé |
| `make psql` | console SQL sur la base |
| `make lint` / `make fmt` | ruff |
| `make clean` | repart de zéro, données comprises |

`gitleaks` et `ruff` tournent en pre-commit, installés par `make setup`. `.env`
est ignoré par git depuis un commit antérieur à l'écriture de toute clé.

---

## Dépannage

| Symptôme | Cause et correctif |
|---|---|
| `port is already allocated` au `make up` | une autre instance de PostgreSQL occupe le port. Le défaut est déjà `5433` ; en changer via `POSTGRES_HOST_PORT` dans `.env` |
| `/ask` répond `503 — le fournisseur LLM a rejeté la clé` | `OPENAI_API_KEY` absente ou invalide dans `.env` |
| `/ask` répond `502` | le fournisseur est indisponible, ou a renvoyé une sortie non conforme au schéma |
| `404` sur `/responses` | la passerelle visée par `OPENAI_BASE_URL` ne sert que `/chat/completions`. Ce projet parle l'API Responses : Gemini et OpenRouter ne conviennent pas en l'état |
| `make eval` traîne, journaux pleins de `429` | plafond de tokens par minute du palier gratuit. Les appels sont rejoués automatiquement ; baisser `CONCURRENCY` dans `eval/runner.py` si le run n'aboutit pas |
| `UnknownModelPricing` au démarrage | le modèle configuré n'est pas dans `src/llm/pricing.py`. L'y ajouter — la table lève plutôt que de publier un coût nul |
| `make ingest` semble lent au premier lancement | normal : le cache PokéAPI se remplit. Les réingestions suivantes sont instantanées |
| le rechargement à chaud d'uvicorn ne réagit pas | inotify ne traverse pas certains montages (réseau, WSL). `WATCHFILES_FORCE_POLLING=true` est déjà posé sur le service `api` |
| un `uv run` nu recrée un venv lent dans le dépôt | passer par `make`, qui pose `UV_PROJECT_ENVIRONMENT` hors du dépôt. Aucun fichier de configuration `uv` ne permet de le fixer |
| `make test` échoue sur la base | les tests ne touchent pas PostgreSQL. Si un test réseau apparaît, c'est un bug : aucun test du dépôt ne doit sortir |

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

En attendant, `OPENAI_BASE_URL` suffit à viser une autre passerelle — à une
condition qui n'est pas cosmétique : **ce provider parle l'API Responses**, pas
`/chat/completions`. La quasi-totalité des services dits « compatibles OpenAI »
s'arrêtent à `/chat/completions` et renvoient un 404 sur `/responses` ; Gemini et
OpenRouter en font partie. Groq l'implémente, d'où le choix.

Deux conséquences visibles dans le code. `uses_reasoning_controls()` découpe le
préfixe éditeur avant de tester la famille, sinon `openai/gpt-oss-120b`
retomberait silencieusement sur `temperature` — que ce modèle accepte, donc sans
erreur pour le signaler. Et le nom rapporté par `/health` est l'hôte réellement
interrogé, pas la chaîne `openai` : une sonde de santé qui annonce le mauvais
fournisseur ne vaut rien, et le nom entre dans la clé de cache, ce qui empêche
deux endpoints servant le même modèle de se répondre l'un l'autre.

### Limites connues, dans la source

`game_indices` de PokéAPI, qui alimente les apparitions par jeu, est renseigné
pour les générations I à VII et **vide au-delà**. PokéAPI expose par ailleurs des
formes non canoniques (`raichu-mega-x` n'existe pas dans les jeux). Les deux
sont des propriétés de la source, pas de l'ingestion — à garder en tête en
écrivant les questions d'évaluation.

---

## Feuille de route

| Jalon | Contenu | État |
|---|---|---|
| 1 | Socle : API, couche LLM instrumentée, base de faits | **terminé** |
| 2 | 40 questions d'évaluation, harnais, ligne de base chiffrée | **terminé** |
| 3 | Outil SQL contraint, RAG avec citations obligatoires | à venir |
| 4 | Agent LangGraph, cas dégradés, traces Langfuse | |
| 5 | CI, image publiée, front React, comparatif multi-fournisseurs | |

---

## Sources & licences

| Source | Usage | Licence |
|---|---|---|
| [PokéAPI](https://pokeapi.co) | stats, espèces, types, évolutions, jeux | données libres ; politique d'usage imposant le cache local |
| [TCGdex](https://tcgdex.net) | illustrateurs des cartes — vérité terrain de l'évaluation | données communautaires ouvertes, usage non commercial |
| [Bulbapedia](https://bulbapedia.bulbagarden.net) | lore et contexte *(jalon 3)* | CC BY-NC-SA — attribution obligatoire |

Le plan initial visait [pokemontcg.io](https://pokemontcg.io) ; son point d'entrée
`/v2/cards` renvoyait `502` au moment du jalon 2. TCGdex répond sans clé et expose
directement le champ `illustrator` — chaque question illustrateur porte l'identifiant
de carte qui permet de la revérifier en une commande.

**Pokémon est une marque de Nintendo / Creatures / GAME FREAK.** Ce projet est
un travail personnel non commercial, sans affiliation. Aucune image de carte ni
ressource graphique sous droits n'est redistribuée dans ce dépôt : seules des
données factuelles issues d'API publiques y sont ingérées, et le cache local
reste hors du dépôt.
