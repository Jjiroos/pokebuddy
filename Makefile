# Le venv vit hors du dépôt : les I/O sont lentes sur un montage réseau ou
# Windows, et aucun réglage de fichier `uv` ne permet de fixer cet emplacement.
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/pokebuddy

LIMIT ?=
MODEL ?=
PERSONA ?=
RUN ?=
SUITE ?=

# Les cibles qui tournent hors conteneur chargent .env comme Compose le fait
# pour le service api : la couche LLM lit OPENAI_API_KEY dans l'environnement
# du processus, pas dans Settings.
DOTENV = set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: setup up down logs migrate revision ingest ingest-tcg ingest-lore eval report test lint fmt psql clean

setup:                       ## venv + dépendances + hooks git
	uv sync --all-groups
	uv run pre-commit install

up:                          ## démarre db + api
	docker compose up -d --build
	docker compose ps

down:                        ## arrête tout (garde les données)
	docker compose down

logs:
	docker compose logs -f api

migrate:                     ## applique les migrations
	docker compose run --rm api alembic upgrade head

# Sur l'hôte, pas dans le conteneur : /app/src y est monté en lecture seule et
# alembic doit écrire le fichier de migration. C'est aussi pour cela que .env
# porte des URL en localhost (le conteneur reçoit les siennes via compose).
revision:                    ## make revision M="message"
	$(DOTENV) uv run alembic revision --autogenerate -m "$(M)"

ingest:                      ## make ingest [LIMIT=50]
	docker compose run --rm api python -m src.ingest.pokeapi $(if $(LIMIT),--limit $(LIMIT),)

ingest-tcg:                  ## make ingest-tcg [LIMIT=500] — cartes et illustrateurs
	docker compose run --rm api python -m src.ingest.tcgdex $(if $(LIMIT),--limit $(LIMIT),)

ingest-lore:                 ## make ingest-lore [LIMIT=50] — entrées de Pokédex + plongements
	docker compose run --rm api python -m src.ingest.lore $(if $(LIMIT),--limit $(LIMIT),)

eval:                        ## make eval [SUITE=principal|lore|multi MODEL=... PERSONA=... LIMIT=n]
	$(DOTENV) uv run python -m eval.runner $(if $(SUITE),--suite $(SUITE),) $(if $(MODEL),--model $(MODEL),) $(if $(PERSONA),--persona $(PERSONA),) $(if $(LIMIT),--limit $(LIMIT),)

report:                      ## make report RUN=eval/runs/<fichier>.json
	uv run python -m eval.report $(RUN)

test:
	uv run pytest -q

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

psql:
	docker compose exec db psql -U pokebuddy -d pokebuddy

clean:                       ## repart de zéro, données comprises
	docker compose down -v
