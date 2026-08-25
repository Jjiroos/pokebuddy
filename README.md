# Pokébuddy

Un agent qui répond à des questions sur les Pokémon en interrogeant une base de
données et un corpus documentaire, puis en citant ses sources — plutôt qu'en
répondant de mémoire.

**Le sujet réel n'est pas le Pokédex : c'est de mesurer de combien un agent
outillé bat un LLM nu sur des faits vérifiables.** Le dépôt est construit autour
de cette mesure, et l'historique des runs d'évaluation en est le livrable.

> **État : jalon 3 sur 5 terminé.** Le LLM nu répondait juste à **53,8 %** des
> questions. Avec un outil SQL contraint, il monte à **92,5 %** — sur les mêmes
> 40 questions, écrites avant d'avoir vu le système échouer.

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

| Catégorie | LLM nu | + SQL | Écart |
|---|---|---|---|
| Factuel — stats, types, évolutions | 27/30 (90,0 %) | 28/30 (93,3 %) | +3,3 pts |
| Agrégation — comptages, filtres, tris | 10/20 (50,0 %) | 18/20 (90,0 %) | **+40,0 pts** |
| Illustrateurs — cartes JCC | 4/20 (20,0 %) | 20/20 (100,0 %) | **+80,0 pts** |
| Pièges — formes régionales, génération 9 | 2/10 (20,0 %) | 8/10 (80,0 %) | **+60,0 pts** |
| **Ensemble** | **43/80 (53,8 %)** | **74/80 (92,5 %)** | **+38,8 pts** |

| Mesure | LLM nu | + SQL |
|---|---|---|
| Coût pour 80 questions | 0,0088 $ | 0,0180 $ |
| Latence médiane | 2,0 s | 5,7 s |
| Appels au modèle par question | 1 | 2 |

L'écart entre personas est nul : 92,5 % des deux côtés. La mise en scène du
Pokédex ne dégrade pas l'exactitude.

**Le pipeline coûte deux fois plus cher et répond trois fois moins vite.** C'est
le prix de l'outil, et il est écrit ici plutôt que tu : deux appels au modèle par
question, et un schéma de ~700 tokens envoyé à chaque génération de requête.

### Ce que le chiffre cache

L'exactitude globale mélange deux régimes très différents :

| | Questions | Justes |
|---|---|---|
| L'outil SQL a produit une requête exécutée | 74/80 | **72 — 97,3 %** |
| Repli sur la réponse de mémoire | 6/80 | 2 — 33,3 % |

**Quand l'outil part, il se trompe presque jamais.** Tout ce qui reste à gagner
est dans les six questions où la requête n'a pas abouti. Les trois défauts
résiduels sont identifiés, et aucun n'est un problème de SQL :

1. **L'échappement d'apostrophe.** Sur « L'appel des Légendes », le modèle écrit
   `'L\'appel des Légendes'` — la convention MySQL. PostgreSQL veut `''`. La
   requête ne renvoie rien, le pipeline replie sur la mémoire, et le modèle
   répond « Mitsuhiro Arita » là où la base dit « sui ». Une note de dialecte
   dans le prompt corrigerait ce cas ; elle attend le jalon 4, où un run complet
   est de toute façon prévu.
2. **Une jointure contradictoire** sur l'évolution de Nymphali, où le modèle
   filtre aussi `species.name = 'nymphali'` alors que cette colonne contient
   l'identifiant anglais `sylveon`.
3. **Une agrégation** où la requête proposée a été rejetée et où la réponse de
   mémoire invente une vitesse de 145 pour Hastacuda, qui en a 136.

### Le modèle sait mieux qu'avant qu'il ne sait pas

| Le modèle a… | LLM nu | + SQL |
|---|---|---|
| répondu juste | 0,96 | 0,99 |
| répondu faux | 0,73 | **0,58** |

L'écart de calibration passe de 0,23 à 0,41. Ce n'est pas un effet secondaire :
quand la base ne renvoie rien, l'invite demande explicitement de le dire plutôt
que de combler. La question sur Nymphali est perdue avec une confiance de **0,10**
— le modèle avoue. Sur la ligne de base, il se trompait sur Axoloto de Paldea à
**0,97**.

### Reproductibilité

La famille GPT-5 refuse `temperature=0` ; la garantie repose donc sur le cache
disque. Chaque série le vérifie : rejouée, elle rend les mêmes réponses au mot
près, à coût quasi nul.

Le palier gratuit de Groq plafonne à 8 000 tokens/minute, et le premier run du
jalon 3 a perdu **20 questions sur 80** en `429`. Elles sont comptées fausses —
un échec est un résultat, jamais une exception. Comme les échecs ne sont
délibérément **pas** mis en cache, rejouer ne repaie que les questions perdues :
57/80 puis 71/80 puis 74/80, pour 0,0143 $ puis 0,0031 $ puis 0,0006 $.

`eval/runs/` contient le premier run de chaque série — celui qui a réellement
appelé le fournisseur, et d'où viennent coût et latence — et le run de référence
sans erreur, d'où vient l'exactitude. `python -m eval.report <neuf>.json --contre
<ancien>.json` refuse de comparer deux runs dont l'empreinte de questions
diffère, et signale de lui-même un run trop servi par le cache pour que son coût
veuille dire quelque chose.

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

**Ce qui existe au jalon 3** : l'API, la couche LLM instrumentée, la base de
faits, les ~23 500 cartes du JCC, et **l'outil SQL**. Le routeur, le RAG et le
calcul restent vides.

Pas encore de routeur, donc : `/ask` est un pipeline en deux temps.

```
   question ──▶ ① le modèle écrit une requête    (ou répond « pas de SQL »)
                        │
                        ▼
                  validation + exécution         (lecture seule, LIMIT forcé)
                        │
                        ▼
   réponse ◀── ② le modèle rédige depuis les lignes  + la requête en source
```

Quand le premier temps renonce, ou que la requête est refusée, on **replie sur
la réponse de mémoire** — le chemin du jalon 1. Ce repli n'est pas une facilité :
refuser de répondre hors périmètre ferait chuter les catégories que le SQL ne
couvre pas, et le tableau mesurerait alors le refus autant que le gain.

**La décision structurante** : les questions d'agrégation (« quels Pokémon Eau
dépassent 100 en vitesse ? ») partent en SQL, pas en RAG. Un index vectoriel est
mauvais en comptage et en filtrage numérique. Le lore partira en RAG au jalon 4 —
mais seulement une fois que des questions de lore existeront pour le mesurer.

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
make ingest-tcg              # ~23 500 cartes du JCC et leurs illustrateurs
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
| `make ingest-tcg` | cartes et illustrateurs, depuis TCGdex |
| `make eval` | rejoue les 40 questions d'évaluation |
| `make report RUN=eval/runs/<fichier>.json` | régénère le tableau d'un run passé |
| `python -m eval.report <neuf>.json --contre <ancien>.json` | tableau avant/après |
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
| `permission denied for table …` dans les journaux | le rôle en lecture seule n'existe pas encore : `make migrate` |
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

### Laisser un modèle écrire du SQL, sans lui laisser casser la base

Quatre couches, et **elles ne se valent pas** — c'est le point qui compte :

| Couche | Ce qu'elle arrête |
|---|---|
| **Rôle PostgreSQL en lecture seule** | tout ce qui écrit, même si le reste a échoué |
| Validation d'AST (`sqlglot`) | instruction multiple, écriture, table hors liste blanche |
| `LIMIT` forcé par réécriture d'arbre | les résultats qui saturent la fenêtre de contexte |
| `statement_timeout` | les jointures cartésiennes |

Les trois dernières sont des **filtres**, et un filtre finit par se contourner.
Seule la première est une propriété du serveur : `DROP TABLE cards` échoue faute
de droits, quelle que soit l'ingéniosité de la requête. Un projet qui n'aurait
que le parseur aurait compris le problème à moitié.

Le contournement le plus élégant est la **CTE écrivante** :

```sql
WITH x AS (INSERT INTO types VALUES (1, 'a') RETURNING *) SELECT * FROM x
```

L'expression racine est un `SELECT`. Un validateur qui n'inspecterait que le
premier nœud la laisserait passer — d'où le parcours de l'arbre entier.
`tests/test_sql_tool.py` porte un test par contournement, nommé d'après lui.

La liste blanche des tables est **dérivée de `Base.metadata`**, jamais écrite à
la main : une table ajoutée plus tard ne peut pas être oubliée, et
`pg_catalog` comme `information_schema` tombent sans avoir à être nommés. Le
schéma envoyé au modèle vient de la même source, pour qu'il ne puisse pas
décrire une base qui n'existe plus.

### Les noms français appartiennent à la base, pas à l'invite

PokéAPI est anglophone : `gyarados`, `water`. Les questions sont françaises :
« Léviator », « Eau ». La tentation est de mettre la correspondance dans le
prompt ; elle a été mise en base — `species.name_fr`, `types.name_fr`,
`card_sets.name_fr` — parce qu'une table de correspondance dans une invite est
une donnée dupliquée, non testable, et invisible à qui lit le schéma.

Les deux premières versions de l'outil s'y sont cassé les dents de façon
instructive : le modèle écrivait `t.name = 'Water'` puis `p.name =
'axoloto-paldea'`, deux valeurs qui n'existent pas. La première a été corrigée
en ingérant les noms de types français ; la seconde en écrivant dans les notes
du schéma que le préfixe d'une forme régionale est l'identifiant **anglais** de
l'espèce.

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
| 3 | Outil SQL contraint, cartes du JCC, citations obligatoires | **terminé** |
| 4 | Agent LangGraph, RAG sur le lore, cas dégradés, traces Langfuse | à venir |
| 5 | CI, image publiée, front React, comparatif multi-fournisseurs | |

---

## Sources & licences

| Source | Usage | Licence |
|---|---|---|
| [PokéAPI](https://pokeapi.co) | stats, espèces, types, évolutions, jeux | données libres ; politique d'usage imposant le cache local |
| [TCGdex](https://tcgdex.net) | ~23 500 cartes, leurs extensions et leurs illustrateurs | données communautaires ouvertes, usage non commercial |
| [Bulbapedia](https://bulbapedia.bulbagarden.net) | lore et contexte *(jalon 3)* | CC BY-NC-SA — attribution obligatoire |

Le plan initial visait [pokemontcg.io](https://pokemontcg.io) ; son point d'entrée
`/v2/cards` renvoyait `502` au moment du jalon 2. TCGdex répond sans clé et expose
directement le champ `illustrator` — chaque question illustrateur porte l'identifiant
de carte qui permet de la revérifier en une commande.

L'ingestion utilise l'**API GraphQL** de TCGdex, qui sert `illustrator` en lot :
une vingtaine de requêtes pour ~23 500 cartes, contre autant de requêtes REST que
de cartes. Les noms français des extensions et des cartes viennent du REST
localisé, une requête par extension. Tout est mis en cache sur disque : la
courtoisie minimale envers une API communautaire gratuite.

**Pokémon est une marque de Nintendo / Creatures / GAME FREAK.** Ce projet est
un travail personnel non commercial, sans affiliation. Aucune image de carte ni
ressource graphique sous droits n'est redistribuée dans ce dépôt : seules des
données factuelles issues d'API publiques y sont ingérées, et le cache local
reste hors du dépôt.
