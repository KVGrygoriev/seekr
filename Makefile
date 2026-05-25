.PHONY: help up down build watch logs migrate run-once serve shell psql lint

help:
	@echo "Targets:"
	@echo "  up         Start postgres (and build the app image if needed)"
	@echo "  down       Stop and remove containers"
	@echo "  build      Build the app image"
	@echo "  watch      Auto-rebuild/restart app on source changes"
	@echo "  migrate    Run alembic upgrade head"
	@echo "  run-once   Run the app one-shot (process and exit)"
	@echo "  serve      Run the app in long-running scheduler mode"
	@echo "  logs       Tail postgres logs"
	@echo "  shell      Open a shell in the app image"
	@echo "  psql       Open psql against the local postgres"
	@echo "  lint       Run ruff against src/"

up:
	docker compose up -d postgres

down:
	docker compose down

build:
	docker compose build app

watch:
	docker compose up -d postgres && docker compose watch

migrate:
	docker compose run --rm app alembic upgrade head

run-once:
	docker compose run --rm app /app/scripts/start.sh seekr run-once

serve:
	docker compose up app

logs:
	docker compose logs -f postgres

shell:
	docker compose run --rm --entrypoint /bin/bash app

psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-seekr} -d $${POSTGRES_DB:-seekr}

lint:
	uv run ruff check src
