# Pokébuddy

Un agent qui répond à des questions sur les Pokémon en interrogeant une base de
données et un corpus documentaire, puis en citant ses sources — plutôt qu'en
répondant de mémoire.

**Le sujet réel n'est pas le Pokédex : c'est de mesurer de combien un agent
outillé bat un LLM nu sur des faits vérifiables.** Le dépôt est construit autour
de cette mesure, et l'historique des runs d'évaluation en est le livrable.

> **État : jalon 4 sur 5 terminé.** Sur les 40 questions gelées : **53,8 %** pour
> le LLM nu, **92,5 %** avec l'outil SQL, **93,8 %** avec l'agent complet. Deux
> suites neuves mesurent ce que l'agent ajoute — et l'une des deux dit qu'il
> n'ajoute pas grand-chose : **90 %** en multi-outils, **46,7 %** en lore. Le
> pourquoi est plus intéressant que les chiffres, et il est plus bas.

---

## Démonstration

*(Capture à venir au jalon 5, avec le front.)*

## Évaluation

Trois suites, **65 questions** à réponse fermée, chacune portant sa vérité
terrain et la requête SQL ou l'entrée de Pokédex qui permet de la revérifier.
Chaque suite a été écrite et commitée **avant le code qu'elle mesure**, dans un
commit qui ne contenait qu'elle : les écrire après aurait permis de choisir
celles qu'on sait déjà cassées.

Notation par grader déterministe, sans juge LLM — un juge est un second modèle,
dont l'erreur devrait elle-même être mesurée.

| Suite | Questions | Ce qu'elle mesure | Résultat |
|---|---|---|---|
| principale | 40 × 2 personas | faits structurés — la comparaison historique | **75/80 (93,8 %)** |
| multi-outils | 10 | le corpus identifie l'espèce, la base donne le fait | **9/10 (90,0 %)** |
| lore | 15 | ce que seul le corpus contient | **7/15 (46,7 %)** |

### La régression : les 40 questions gelées

Le run le plus important du jalon n'est pas celui des nouveautés. Un agent qui
dégrade le cas simple pour gagner le cas complexe est un mauvais échange, et
c'est précisément ce qu'un score global unique masquerait.

| Catégorie | LLM nu | + SQL | Agent complet | Écart j3 → j4 |
|---|---|---|---|---|
| Factuel — stats, types, évolutions | 27/30 (90,0 %) | 28/30 (93,3 %) | 29/30 (96,7 %) | +3,3 pts |
| Agrégation — comptages, filtres, tris | 10/20 (50,0 %) | 18/20 (90,0 %) | 20/20 (100,0 %) | +10,0 pts |
| Illustrateurs — cartes JCC | 4/20 (20,0 %) | 20/20 (100,0 %) | 16/20 (80,0 %) | **−20,0 pts** |
| Pièges — formes régionales, génération 9 | 2/10 (20,0 %) | 8/10 (80,0 %) | 10/10 (100,0 %) | +20,0 pts |
| **Ensemble** | **43/80 (53,8 %)** | **74/80 (92,5 %)** | **75/80 (93,8 %)** | **+1,2 pts** |

**Le +1,2 point est le chiffre le moins intéressant du tableau.** Il cache trois
catégories qui gagnent 10 à 20 points et une qui en perd 20. Les trois défauts
résiduels du jalon 3 sont corrigés — l'agrégation Eau passe à 20/20, les pièges
à 10/10 — et une régression neuve les remplace sur les illustrateurs.

**D'où vient cette régression**, comparée requête à requête :

```sql
-- jalon 3, juste : le nom est PROJETÉ
SELECT c.name_en, c.name_fr, c.illustrator ...
 WHERE cs.name_fr = 'Neo Genesis' AND c.local_id = '9'

-- jalon 4, faux : le nom est FILTRÉ, en minuscules
SELECT c.illustrator ...
 WHERE cs.name_fr = 'Neo Genesis' AND c.local_id = '9' AND c.name_en = 'lugia'
```

La base contient `Lugia`, pas `lugia` : la requête s'exécute, ne rend rien, et le
modèle répond honnêtement qu'il ne sait pas. La cause est une **note de schéma
fausse** — elle affirme que *toute* colonne `name` porte l'identifiant PokéAPI en
minuscules, ce qui est vrai de `pokemon`, `species` et `types`, et faux de
`cards`, dont les noms viennent de TCGdex et sont des noms d'affichage. Je ne
sais pas dire laquelle de mes modifications a fait basculer le modèle de projeter
à filtrer ; l'attribuer à l'une d'elles serait une histoire plutôt qu'un constat.

### Les deux suites neuves, et pourquoi l'une marche et l'autre pas

| | Ce que la question donne | Ce qu'elle demande | Résultat |
|---|---|---|---|
| multi-outils | le fait (« mange 400 kg/jour ») | l'espèce, puis un fait en base | **9/10** |
| lore | l'espèce (« Ronflex ») | le fait, dans le corpus | **7/15** |

**C'est le même outil, et il est excellent d'un côté, mauvais de l'autre.** Le
grand livre des appels dit pourquoi — le routeur **invente la réponse** dans la
requête qu'il envoie au corpus :

| Question posée | Requête produite par le routeur | Ce que dit l'entrée réelle |
|---|---|---|
| « que Téraclope cherche-t-il à avaler ? » | « Terraclope cherche à avaler **les nuages**. » | « des **feux follets** » |
| « que se passe-t-il quand Noctali s'expose à la lune ? » | « son corps émet **une lueur argentée** » | « ses **anneaux brillent** » |

La recherche part alors chercher l'invention. Sur les 8 échecs de la suite lore,
**3 ne rapportent aucun passage** — la requête inventée est si loin du corpus que
le plancher de distance la rejette entièrement — et les 5 autres en rapportent un
à cinq, tous hors sujet.

**Ce n'est pas une désobéissance du modèle, c'est un défaut de ma conception.**
Le corpus est déclaratif et le modèle de plongement est symétrique ; c'est ce qui
m'avait fait demander au routeur une reformulation affirmative, et la mesure
avait été franche : sur huit cas, la question brute plaçait 1 bonne espèce en
tête, l'affirmation en plaçait 8. Mais cette mesure avait été faite **sur les
questions multi-outils uniquement** — celles où le fait est donné et l'espèce
manque. Demander la forme affirmative d'une question dont la réponse est
*inconnue* exige d'inventer la réponse : il n'existe aucune forme affirmative de
« que cherche-t-il à avaler ? » qui ne soit pas déjà une réponse. La règle
« n'écris jamais la réponse que tu crois connaître », que j'avais ajoutée en
prévention, demande une chose et son contraire.

**Le correctif est structurel, et il est vérifié plutôt que supposé.** Quand la
question *nomme* l'espèce, la bonne opération est un filtre
(`WHERE lore_chunks.species_id = …`), pas une similarité. Contrôle fait entrée
par entrée : **les 15 réponses sont présentes dans le corpus de l'espèce
nommée** — y compris les trois qu'un premier test automatique avait manquées
pour cause d'accents (Mewtwo « gènes de Mew », Tortank « percer le métal le plus
résistant », Ectoplasma « rôde dans les parages »). Le corpus est complet ; c'est
la recherche qui va au mauvais endroit. La similarité ne devrait servir qu'à
identifier une espèce **non nommée** — le seul cas où elle est le bon outil, et
celui où elle rend 90 %.

### Les défauts résiduels, avec leur cause

Aucun n'est corrigé dans ce jalon, et c'est délibéré : ils ont été découverts en
regardant des échecs notés. Les corriger puis republier le chiffre reviendrait à
régler l'invite sur la copie. Ils ouvrent le jalon 5, exactement comme les trois
défauts du jalon 3 ont ouvert celui-ci.

| # | Défaut | Cause établie |
|---|---|---|
| 1 | Le routeur envoie la question au corpus au lieu de la base | « de quelle génération … ? » — `species.generation` vaut pourtant 8 pour Lanssorien |
| 2 | `c.name_en = 'lugia'` ne rend rien | note de schéma fausse pour `cards` (voir plus haut) |
| 3 | `p.name_fr` n'existe pas | la colonne est sur `species`. Lister les colonnes ne suffit pas, il faut dire l'absence |
| 4 | Le corpus reçoit une requête qui contient déjà une réponse inventée | la reformulation affirmative, hors de son domaine de validité |
| 5 | « qui a illustré **sa** carte n° 4 ? » — `sql` mis à null | `cards` n'a aucun lien vers `species`. **Le modèle a raison** et refuse plutôt qu'inventer ; il sur-résout seulement, extension + numéro suffisant à identifier la carte |

Le cinquième mérite d'être lu en entier, parce que c'est le comportement qu'on
voulait : le modèle explique que la jointure n'existe pas dans le schéma fourni,
et renonce. Une requête inventée aurait donné un résultat faux et crédible.

### Le modèle sait de mieux en mieux qu'il ne sait pas

| Le modèle a… | LLM nu | + SQL | Agent complet |
|---|---|---|---|
| répondu juste | 0,96 | 0,99 | 0,97 |
| répondu faux | 0,73 | 0,58 | **0,13** |

L'écart de calibration passe de 0,23 à 0,41 puis à **0,84**. Ce n'est pas un
effet secondaire : quand une source ne rend rien, l'invite demande de le dire
plutôt que de combler. Les quatre échecs illustrateurs sont perdus en répondant
« je ne sais pas » — le modèle a tort sur le fond et raison sur lui-même. Sur la
ligne de base, il se trompait sur Axoloto de Paldea à **0,97**.

### Coût et latence

| Mesure | LLM nu | + SQL | Agent complet |
|---|---|---|---|
| Coût pour 80 questions, reprises comprises | 0,0088 $ | 0,0180 $ | 0,0223 $ |
| Latence médiane, premier run | 2,0 s | 5,7 s | 10,7 s |
| Appels au modèle par question | 1 | 2 | 2 ou 3 |

**L'agent coûte 24 % de plus que le pipeline SQL et répond deux fois moins
vite, pour 1,2 point.** C'est écrit ici plutôt que tu. Le troisième appel n'est
payé que si la base est sollicitée ; le corpus, lui, ne coûte aucun appel de
modèle, la recherche étant locale. La latence médiane inclut l'attente de
reprise sur les `429` du palier gratuit, qui pèse lourd — les deux autres
colonnes la subissaient aussi, mais moins souvent.

Les deux suites neuves ont coûté 0,0057 $ (lore) et 0,0050 $ (multi).

### Reproductibilité

La famille GPT-5 refuse `temperature=0` ; la garantie repose donc sur le cache
disque. Chaque série le vérifie : rejouée, elle rend les mêmes réponses au mot
près, à coût quasi nul.

**Et le jalon 4 fait monter l'enjeu de cette limite.** La même question, tirée
deux fois sur deux caches distincts, a été routée différemment :

    hôte      needs_db=true, lore_query="Il vole à plus de 300 km/h..."
              → 5 extraits + 1 requête, réponse juste et sourcée
    conteneur needs_db=true, lore_query=null
              → le SQL renonce (aucune colonne « km/h »), zéro source, mémoire

Les deux réponses sont justes sur le fond, une seule est vérifiable. Une
décision de routage amplifie la variabilité bien plus qu'une reformulation de
réponse : les chiffres publiés sont reproductibles **depuis le cache**, mais un
run à cache froid donnerait un résultat voisin, pas identique. C'est une limite
de la mesure, pas un détail d'implémentation.

Le palier gratuit de Groq plafonne à 8 000 tokens/minute, et un agent consomme
plus qu'un pipeline en deux temps : le premier run de la suite principale a perdu
**29 questions sur 80** en `429`, contre 20 au jalon 3. Elles sont comptées
fausses — un échec est un résultat, jamais une exception. Comme les échecs ne
sont délibérément **pas** mis en cache, rejouer ne repaie que les questions
perdues : 47/80 puis 72/80 puis 75/80, pour 0,0166 $ puis 0,0047 $ puis 0,0010 $.

`eval/runs/` contient toute la série de chaque suite : le premier run, seul à
avoir réellement appelé le fournisseur et d'où viennent coût et latence, et le
run de référence sans erreur, d'où vient l'exactitude. `python -m eval.report
<neuf>.json --contre <ancien>.json` refuse de comparer deux runs dont l'empreinte
de questions diffère, et signale de lui-même un run trop servi par le cache pour
que son coût veuille dire quelque chose.

L'empreinte des 40 questions gelées, `b219b76777ad48f5`, est vérifiée par un
test : si elle bouge, le 53,8 % → 92,5 % → 93,8 % cesse d'être comparable.

---

## Architecture

```
  question
     │
     ▼
 ① routeur ········ un appel : quelles sources ? (needs_db, lore_query)
     │
     ├─ si lore_query ──▶  RAG Tool · pgvector, 5 233 entrées de Pokédex
     │                     aucun appel au modèle — la recherche est locale
     │                          │
     │                          └── l'espèce est identifiée, et sert de filtre
     │                          ▼
     ├─ si needs_db ────▶  SQL Tool · ② un appel écrit la requête,
     │                     puis on l'exécute (lecture seule, LIMIT forcé)
     │
     ▼
 ③ rédaction ······ depuis les sources récoltées, et rien d'autre
     │
     ▼
  réponse + citations + trace
```

**C'est une chaîne à sauts conditionnels, pas un éventail parallèle**, et le
domaine l'impose : la requête SQL ne peut pas s'écrire avant que le corpus ait
dit *de quel Pokémon on parle*. « Quel Pokémon mange 400 kg par jour, et quel
est son numéro national ? » n'est traduisible en SQL qu'une fois Ronflex
identifié. Le parallélisme qu'offre LangGraph n'est donc pas utilisé, et le
dire vaut mieux que de laisser croire le contraire : ce que le framework
apporte réellement ici, ce sont des étapes nommées et un état typé, qui se
transposent un pour un en spans de trace.

Selon la question, le pipeline coûte **deux ou trois appels** au modèle : le
routeur, la rédaction, et la génération de SQL seulement si la base est
sollicitée. Le corpus, lui, ne coûte aucun appel — la recherche vectorielle est
locale.

**`src/llm/` n'est pas touché par le graphe.** Les nœuds appellent le `Protocol`
existant ; aucune abstraction LangChain ne remonte dans la couche fournisseur,
ce qui laisse intacts le cache disque, le grand livre des coûts et le balayage
de modèles.

### Les cas dégradés sont le contenu du jalon, pas le graphe

Un graphe qui enchaîne trois nœuds quand tout va bien n'apporte rien. Ce qui se
mesure, c'est ce qu'il fait quand une source se tait, refuse ou tombe. Aucun de
ces cas ne remonte en erreur HTTP, et chacun a son test.

| Cas | Comportement |
|---|---|
| Le routeur ne choisit aucun outil | réponse de mémoire, `sources` vide |
| Un outil échoue (SQL refusé, corpus en panne) | l'autre continue, la cause est journalisée |
| Les deux échouent | repli mémoire — le chemin nu du jalon 1 |
| Zéro ligne **et** zéro passage trouvé | on le dit au modèle, qui doit l'avouer |
| Question hors périmètre | « aucun outil » est une réponse valable du routeur |

Ce repli n'est pas une facilité. Refuser de répondre hors périmètre ferait
chuter les catégories que les outils ne couvrent pas, et le tableau
d'évaluation mesurerait alors le refus autant que le gain.

Seules deux causes deviennent des erreurs HTTP, et aucune n'est un échec
d'outil : un refus du modèle (422) et une sortie non conforme au schéma
demandé (502). Les avaler produirait une réponse vide et silencieuse.

### Ce qui n'existe pas, et pourquoi

**Il n'y a pas d'outil de calcul.** Aucune des 65 questions n'en demande, et
l'ajouter serait exactement l'erreur que ce jalon évite : empiler un outil qui
ne change aucun chiffre, puis l'appeler une capacité.

**La requête envoyée au corpus n'est pas la question de l'utilisateur.** C'est
la question reformulée en affirmation, par le routeur, dans l'appel qu'il
faisait de toute façon — zéro appel de modèle en plus. La raison est mesurée,
pas supposée : le modèle de plongement est *symétrique*, entraîné sur des
paires de phrases de même nature, et apparier une question interrogative à une
entrée de Pokédex déclarative le met en échec. Sur huit cas multi-outils, la
question brute plaçait **1** bonne espèce en tête ; l'affirmation en place
**8**.

Et **cette mesure a un domaine de validité que je n'avais pas vu** : elle vaut
quand la question *contient* le fait et cherche l'espèce. Quand elle nomme
l'espèce et cherche le fait, la reformulation oblige à inventer la réponse, et
la suite lore le paie — 46,7 % contre 90 % en multi-outils. Le diagnostic
complet est dans la section Évaluation ; c'est le premier chantier du jalon 5.

**La recherche sait ne rien renvoyer.** Un index vectoriel rend *toujours* ses
k plus proches voisins : interrogé sur la capitale de la France, il rend les
cinq entrées de Pokédex les moins éloignées, et un modèle à qui on les tend
finit par en tirer quelque chose. Le plancher de distance est réglé à **0,40**,
sur 24 requêtes prises **hors** du jeu d'évaluation — le calibrer sur les
questions notées reviendrait à s'entraîner sur sa propre copie. Les deux
distributions se chevauchent d'un cheveu, et aucun seuil ne les sépare
parfaitement ; on préfère laisser passer une requête de trop plutôt que
d'amputer le rappel, l'invite de rédaction demandant par ailleurs au modèle
d'ignorer un extrait hors sujet.

**La décision structurante reste celle du jalon 3** : les questions
d'agrégation (« quels Pokémon Eau dépassent 130 en Vitesse ? ») partent en SQL,
pas en RAG. Un index vectoriel est mauvais en comptage et en filtrage
numérique.

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

Le traçage Langfuse est **facultatif**. Sans `LANGFUSE_PUBLIC_KEY` et
`LANGFUSE_SECRET_KEY`, le dépôt tourne à l'identique : le SDK n'est même pas
importé. Cette garantie est portée par notre code et vérifiée par un test, pas
déléguée à une note de version du SDK.

```bash
git clone https://github.com/Jjiroos/pokebuddy.git
cd pokebuddy
cp .env.example .env         # puis coller une vraie clé dans OPENAI_API_KEY
make setup                   # venv, dépendances, hooks git
make up                      # PostgreSQL + pgvector, API
make migrate                 # crée le schéma
make ingest                  # ~1350 pokémon, une vingtaine de secondes
make ingest-tcg              # ~23 500 cartes du JCC et leurs illustrateurs
make ingest-lore             # 5 233 entrées de Pokédex + leurs plongements
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
| `make ingest-lore` | entrées de Pokédex et plongements, depuis le cache existant |
| `make eval` | rejoue les 40 questions d'évaluation, sur les deux personas |
| `make eval SUITE=lore` / `SUITE=multi` | les deux suites du jalon 4, sur une persona |
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
| `make ingest-lore` télécharge 220 Mo au premier lancement | normal : le modèle de plongement ONNX, mis en cache dans `FASTEMBED_CACHE_DIR`. Aucun torch, aucun GPU |
| `Aucune espèce en base : lancer make ingest d'abord` | `make ingest-lore` part des espèces déjà ingérées, pour qu'aucun extrait ne référence une espèce absente |
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

### Le traçage est optionnel *par construction*, pas par bienveillance du SDK

Un pipeline à trois appels et deux outils est illisible dans un journal plat :
c'est là que Langfuse gagne son inscription. Pas sur le coût — `llm_calls` le
mesure déjà mieux, puisqu'il sait ce que le cache a servi.

Langfuse auto-hébergé demande six services, quatre cœurs et 16 Gio de RAM. Le
jalon 5 promet un `docker compose up` qui marche sur une machine vierge : c'est
donc le palier gratuit du cloud, ou rien. Et « ou rien » doit être un mode de
fonctionnement à part entière.

**La promesse « le dépôt tourne sans les clés » est la nôtre, pas celle du
SDK.** Sans paire de clés, `src/obs/tracing.py` rend un objet muet : rien n'est
importé, rien n'est construit, aucune socket n'est ouverte. Se reposer sur « le
SDK attrape ses erreurs » ferait dépendre une garantie du dépôt d'une note de
version d'un tiers. Onze tests la vérifient, dont trois qui simulent un SDK
défaillant, et un qui vérifie l'inverse — que le SDK est réellement appelé
quand les clés sont là, sans quoi « ne rien casser » serait satisfait par un
traçage qui ne trace jamais rien.

La limite du filet est explicite : le `try` couvre la construction du span, pas
son corps. Avaler l'exception de l'appelant ferait d'un outil d'observation une
cause de panne muette.

**Ce qui n'est pas vérifié** : le rendu côté `cloud.langfuse.com`. Aucune clé
n'existe encore sur ce dépôt. Le code est écrit contre l'API du SDK 4.14, et la
seule chose prouvée aujourd'hui est qu'il n'a aucun effet sans clés.

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

`cards` ne porte aucun lien vers `species` : TCGdex et PokéAPI sont deux sources
sans identifiant commun. Une question qui demande « la carte de *ce* Pokémon »
n'est donc pas traduisible en une requête, et le générateur de SQL le dit au lieu
d'inventer une jointure — c'est le défaut résiduel n° 5.

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
| 4 | Agent LangGraph, RAG sur le lore, cas dégradés, traces Langfuse | **terminé** |
| 5 | Défauts résiduels (recherche par espèce d'abord), CI, image publiée, front React, comparatif multi-fournisseurs | |

---

## Sources & licences

| Source | Usage | Licence |
|---|---|---|
| [PokéAPI](https://pokeapi.co) | stats, espèces, types, évolutions, jeux | données libres ; politique d'usage imposant le cache local |
| [TCGdex](https://tcgdex.net) | ~23 500 cartes, leurs extensions et leurs illustrateurs | données communautaires ouvertes, usage non commercial |
| [PokéAPI](https://pokeapi.co) — `flavor_text_entries` | le corpus de lore, en français | mêmes données, même politique de cache |

**Le corpus de lore ne vient pas d'un scraping.** Le plan initial prévoyait
Bulbapedia ; il n'a pas servi. Les entrées de Pokédex françaises dormaient déjà
dans le cache d'ingestion du jalon 1 — chaque payload `pokemon-species` de
PokéAPI porte ses `flavor_text_entries` localisées, que l'ingestion
téléchargeait sans les lire. Ni scraping, ni nouvelle licence, ni appel réseau
supplémentaire : `make ingest-lore` affiche « 1025 servis, 0 téléchargés », ce
qui rend la promesse vérifiable plutôt que déclarative.

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
