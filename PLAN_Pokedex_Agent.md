# Pokédex Agent — Guideline & plan de projet

> Projet portfolio GenAI. Objectif : démontrer en deux semaines de soirées qu'on sait
> appeler un LLM par code, l'outiller, **mesurer** ce qu'il produit et le livrer proprement.

---

## 1. Le pitch en trois phrases

Un agent qui répond à des questions sur les Pokémon comme le Pokédex du dessin animé : stats,
évolutions, apparitions par jeu, et les illustrateurs des cartes du TCG.

Il ne répond pas de mémoire — il interroge une base de données et un corpus documentaire, puis cite ses sources.

**Le vrai sujet du projet n'est pas le Pokédex : c'est de prouver, chiffres à l'appui, qu'un agent
outillé bat un LLM nu sur des faits vérifiables.**

---

## 2. Pourquoi ce sujet est un bon sujet

| Critère | Pourquoi ça compte |
|---|---|
| **Vérité terrain exacte** | Une stat de base ou un nom d'illustrateur ne se discute pas → évaluation objective |
| **Hallucinations mesurables** | Le LLM nu se trompe beaucoup → l'écart avant/après est spectaculaire et honnête |
| **Corpus hybride** | Données tabulaires *et* textuelles → impose un vrai routage entre outils, pas un RAG de plus |
| **Motivation** | Un projet de soirées ne survit que s'il est plaisant. Critère n°1 : il sera fini |

**Le risque** : être classé « projet jouet ». Il se neutralise par le README (§8) : si la première
chose visible est un tableau d'évaluation et un schéma d'architecture, c'est l'ingénierie qu'on voit.

---

## 3. Architecture cible

```
                    ┌─────────────┐
   question ───────▶│   Agent     │  (LangGraph)
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
                    │  Réponse    │  + citations + trace Langfuse
                    └─────────────┘
```

**La décision structurante :** les questions d'agrégation (« quels Pokémon Eau ont plus de 100 en
vitesse ? », « combien de cartes Arita a-t-il illustrées ? ») partent en **SQL**, pas en RAG.
Un index vectoriel est mauvais en comptage et en filtrage numérique. Le lore et les anecdotes
partent en **RAG**. Le routeur arbitre — et c'est là qu'est le travail d'ingénierie du projet.

---

## 4. Stack

| Couche | Choix | Justification |
|---|---|---|
| API | **FastAPI** + Pydantic | Stack de l'offre ; sorties structurées validées |
| Agent | **LangGraph** | Graphe explicite, états inspectables, reprise sur erreur |
| Observabilité | **Langfuse** (auto-hébergé) | Stack de l'offre ; traces, coûts, comparaison de runs |
| Base | **PostgreSQL + pgvector** | Une seule base pour le relationnel *et* le vectoriel |
| Embeddings | **local** (`fastembed`, multilingue) | Gratuit, CPU, reproductible, pas d'appel réseau |
| Génération | **API** (Claude en dev) derrière une abstraction | Voir §5 |
| Front | **React** minimal | « interfaces de test » de l'annonce ; réutilise tes compétences |
| CI | **GitHub Actions** | Tests + éval à chaque push |

**Ne pas empiler les frameworks.** LangGraph + Langfuse suffisent. Les autres frameworks agentiques
(Openclaw, Hermes Agent…) se gardent pour la conversation en entretien, pas pour le code : un dépôt
qui en utilise quatre suggère qu'on n'en maîtrise aucun.

---

## 5. LLM : API ou local ?

**API pour la génération. Local uniquement comme axe de comparaison mesuré.**

Faire tourner un modèle local comme moteur principal, c'est trois soirées de quantification et de
VRAM volées au sujet réel du poste.

### Abstraction fournisseur — obligatoire

Matera utilise Gemini, Mistral et OpenAI. Coder en dur un seul fournisseur serait un mauvais signal.

```python
# llm/provider.py
class LLMProvider(Protocol):
    def complete(self, messages, tools=None, temperature=0.0) -> LLMResponse: ...


# AnthropicProvider / MistralProvider / OllamaProvider
# Sélection : LLM_PROVIDER=anthropic|mistral|ollama
```

Développer avec un fournisseur, en ajouter un second en une soirée, et publier le comparatif.
Ligne de README : *« changer de fournisseur = une variable d'environnement »*.
LangChain fournit déjà cette abstraction (`ChatAnthropic`, `ChatMistralAI`, `ChatOllama`).

### Réflexes de production à mettre dès le jour 1

- `temperature=0` partout (reproductibilité de l'éval)
- Retry avec backoff exponentiel sur 429 / 5xx, timeout explicite
- **Cache disque** des appels : clé = hash(modèle + prompt + paramètres) → l'éval se relance
  gratuitement et donne le même résultat
- **Compteur de tokens** dès le premier appel : `input_tokens`, `output_tokens`, coût estimé,
  affichés comme **coût par requête** dans le tableau d'éval

### Secrets

- `.env` dans `.gitignore` **avant** d'écrire la première clé ; `.env.example` commité avec des valeurs bidon
- Hook pre-commit `gitleaks` ou `detect-secrets` (2 min d'installation)
- `os.environ["ANTHROPIC_API_KEY"]` — jamais de `.get()` avec valeur par défaut en dur

---

## 6. Données & licences

| Source | Contenu | À savoir |
|---|---|---|
| **PokéAPI** | Stats, espèces, types, évolutions, apparitions par jeu | Complète, bien documentée |
| **Pokémon TCG API** (pokemontcg.io) | Cartes, séries, **champ `artist`** | La pépite pour l'angle illustrateurs |
| **Bulbapedia** | Lore, anecdotes de design, contexte des séries | Licence CC BY-NC-SA → attribution obligatoire |

**Pokémon est une IP Nintendo.** Ne pas redistribuer les images de cartes dans le dépôt, rester en
usage non commercial, écrire une section **Sources & licences** dans le README.

> Un recruteur qui voit ce paragraphe sur un projet fun comprend que tu penses conformité même quand
> personne ne regarde. Signal fort, surtout chez une boîte qui traite des données clients.

---

## 7. Les 5 jalons

### Jalon 1 — Le socle (un week-end)
- `docker-compose.yml` : Postgres + pgvector, API, (Ollama, Langfuse en option à ce stade)
- FastAPI : `POST /ask`, `POST /extract`, `GET /health`
- **Un appel LLM direct, sans outil, sans RAG** — sortie structurée validée par Pydantic
- Ingestion PokéAPI → tables relationnelles

✅ *Sortie attendue : ça répond, et ça répond souvent mal. C'est la ligne de base.*

### Jalon 2 — L'évaluation (une soirée) — **le jalon à ne pas sauter**
- **40 questions** avec réponses attendues, écrites AVANT toute amélioration
- Sinon : optimisation vers son intuition plutôt que vers un résultat
- Répartition suggérée :
  - 15 questions factuelles simples (stats, types, évolutions)
  - 10 questions d'agrégation (comptages, filtres, tris)
  - 10 questions illustrateurs / TCG
  - **5 pièges** : formes régionales, Pokémon récents, illustrateurs peu connus — là où la mémoire
    du modèle est notoirement fausse
- `eval.py` → exactitude, qualité des citations, latence p95, coût par requête
- **Mesurer la ligne de base et garder le chiffre**, même médiocre : il rendra la suite lisible

### Jalon 3 — SQL + RAG (2 soirées)
- Outil SQL : text-to-SQL contraint (schéma en prompt, requêtes en lecture seule, `LIMIT` forcé)
- Ingestion du corpus textuel → découpage, embeddings locaux, pgvector
- Outil RAG avec **citations obligatoires** dans la réponse
- Relancer l'éval, noter le gain par catégorie de questions

### Jalon 4 — L'agent (2 soirées)
- LangGraph : routeur + 3 outils (SQL, RAG, calcul)
- Gestion des cas dégradés : outil qui échoue, résultat vide, question hors périmètre
- **Langfuse** branché : traces, coûts, comparaison entre runs d'éval
- Relancer l'éval

### Jalon 5 — Industrialisation (un week-end) — *ton avantage naturel*
- GitHub Actions : lint + tests + éval à chaque push, résultats en artefact
- Image Docker publiée, `docker compose up` qui marche du premier coup sur une machine vierge
- Front React minimal : champ de question, réponse, sources, trace
- Comparatif final multi-fournisseurs (§9)

> **Règle d'arrêt :** mieux vaut les jalons 1 à 3 finis et propres que les 5 à moitié.
> Un dépôt marqué « work in progress » ne sert à rien en candidature.

---

## 8. Le README est le vrai livrable

Ce qu'un recruteur lit en trois minutes, **dans cet ordre** :

1. **Le problème** en deux phrases
2. **Un GIF ou une capture** de la démo
3. **Le tableau d'évaluation** — avant / après, c'est le cœur
4. **Le schéma d'architecture** (celui du §3 suffit)
5. **Les décisions techniques et leurs raisons** — pourquoi SQL plutôt que RAG sur l'agrégation,
   pourquoi embeddings locaux, pourquoi ce découpage
6. **Ce que je ferais différemment en production** ← *cette section vaut de l'or en entretien*
7. **Sources & licences**

---

## 9. Les deux idées qui rendent le projet mémorable

### La persona comme variable mesurée
Réponse en voix de Pokédex par défaut, mode factuel en option — puis **faire tourner l'éval dans les
deux modes et publier l'écart**. « Est-ce que la persona dégrade l'exactitude ? » est une vraie
question de production que presque personne ne mesure.

### Le comparatif d'arbitrage

| Config | Exactitude | Latence p95 | Coût / requête |
|---|---|---|---|
| LLM nu (sans outil) | *ligne de base* | | |
| API — modèle rapide | | | |
| API — modèle raisonneur | | | |
| Local (Ollama) | | | 0 € |

La conclusion sera probablement « le local perd sur le tool-calling mais coûte zéro et garde la
donnée à la maison ». **C'est un arbitrage d'ingénieur, pas un résultat de benchmark** — et c'est
exactement le raisonnement attendu en entretien technique.

---

## 10. Structure du dépôt

```
pokedex-agent/
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore                 # .env dès le premier commit
├── .pre-commit-config.yaml    # gitleaks
├── src/
│   ├── api/                   # FastAPI : routes, schémas Pydantic
│   ├── llm/                   # provider.py, cache.py, tokens.py
│   ├── agent/                 # graphe LangGraph, outils
│   ├── ingest/                # pokeapi.py, tcg.py, lore.py
│   └── db/                    # modèles, migrations
├── eval/
│   ├── questions.yaml         # les 40 questions
│   ├── run_eval.py
│   └── results/               # tableaux versionnés → progression visible
├── frontend/                  # React minimal
└── tests/
```

**Versionner les résultats d'éval** dans `eval/results/` : l'historique des runs montre la
progression du projet, ce qui est en soi une démonstration de méthode.

---

## 11. Erreurs à éviter

- ❌ Écrire les questions d'éval **après** avoir amélioré le système
- ❌ Empiler les frameworks agentiques pour faire riche
- ❌ Viser le chatbot généraliste plutôt qu'un périmètre étroit et mesurable
- ❌ Laisser le dépôt en « work in progress »
- ❌ Redistribuer les images de cartes
- ❌ Attendre que le projet soit fini pour postuler → **postuler maintenant, mentionner le projet en cours**

---

## 12. Ce que ce projet prouve (et ce qu'il ne prouve pas)

**Il prouve** : usage programmatique de LLMs, tool-calling, RAG, évaluation rigoureuse, FastAPI,
conteneurisation, CI/CD, arbitrages de coût et de fournisseur.

**Il ne prouve pas** : une expérience GenAI en production à l'échelle, du fine-tuning, du RLHF.
Ne pas le présenter comme tel — le niveau visé ici est **intermédiaire**, et c'est précisément ce
qui est recherché.
