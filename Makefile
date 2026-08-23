# Le venv ne vit pas sur /mnt/z : les I/O y sont lentes sous WSL2.
export UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/pokebuddy

LIMIT ?=

.PHONY: setup up down logs migrate revision ingest test lint fmt psql clean

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

revision:                    ## make revision M="message"
	docker compose run --rm api alembic revision --autogenerate -m "$(M)"

ingest:                      ## make ingest [LIMIT=50]
	docker compose run --rm api python -m src.ingest.pokeapi $(if $(LIMIT),--limit $(LIMIT),)

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
