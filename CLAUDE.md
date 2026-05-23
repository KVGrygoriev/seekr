# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Docker-based workflow (all runtime operations go through Docker)
make up          # Start postgres in background
make down        # Stop and remove containers
make build       # Rebuild app Docker image
make migrate     # Run alembic upgrade head against running postgres
make run-once    # Single pipeline execution (fetch → classify → report → exit)
make serve       # Long-running daemon (respects schedule config)
make shell       # Bash shell inside the app container
make psql        # psql against running postgres
make logs        # Tail postgres logs

# Linting (runs locally via uv)
make lint        # ruff check src/

# CLI (inside container or with uv run)
seekr run-once [--config PATH]    # One-shot pipeline
seekr serve [--config PATH]       # Continuous with scheduling
seekr check-config [--config PATH] # Validate config and print summary
```

**No test suite exists.** Use `seekr check-config` to validate config changes and `make run-once` to smoke-test end-to-end.

## Architecture

Seekr is an OLX.ua listing monitor: it scrapes search result pages, detects new/changed/reposted listings, and sends grouped Telegram reports. It is backend-only — no HTTP API, no UI.

### Pipeline (`pipeline.py`)

Single orchestration function called by both `run-once` and `serve` modes:

```
OlxAdapter.fetch_listings()
  → OlxParser (selectolax HTML → RawListing objects)
  → DiffEngine.classify()
    → fingerprint match vs DB + external_id comparison
    → returns ClassifiedListing (NEW | UPDATED_BY_OWNER | REPOSTED_BY_OTHER | PRICE_CHANGED | UNCHANGED)
  → Repository.persist_delta()
  → should_dispatch() filter
  → ReportBuilder.build()
    → Jinja2 template → Telegram HTML messages
  → TelegramClient.send()
  → Repository.record_dispatch()
```

### Classification logic (`diff/engine.py`)

Uses two keys together: `external_id` (OLX listing ID) and `fingerprint` (normalized `title|location|area_m2`):

| Case | Classification |
|------|---------------|
| No match anywhere | NEW |
| Same external_id, content changed | UPDATED_BY_OWNER |
| Different external_id, same fingerprint | REPOSTED_BY_OTHER |
| Same external_id, price differs | PRICE_CHANGED |
| Nothing changed | UNCHANGED (not dispatched) |

`should_dispatch()` in `diff/engine.py` guards re-sends: a listing is skipped if it was already dispatched with the same classification and price.

### Source adapter pattern (`sources/`)

`SourceAdapter` is a `Protocol` in `sources/base.py`. Adding a new source: implement the protocol in `sources/<name>/adapter.py`, then register it in `pipeline._adapter_for()`. OLX is the only current implementation.

### Configuration (`config.py`)

Pydantic model loaded from YAML. Supports `${ENV_VAR}` and `${VAR:-default}` interpolation in YAML values. The YAML config controls searches (URLs to monitor), report grouping/sorting/filtering, Telegram settings, and schedule interval.

### Database (`db/`)

SQLAlchemy 2.0 async ORM with asyncpg. All DB access goes through `Repository` in `db/repository.py` — no raw SQL elsewhere. Alembic manages migrations in `migrations/versions/`.

Key tables: `listings` (current state), `listing_history` (change log), `report_dispatches` (dedup ledger), `operator_notes` (manual flags).

### Scheduler (`scheduler.py`)

APScheduler wraps the pipeline for `serve` mode. `run-once` mode calls the pipeline directly and exits.

### Logging (`logging.py`)

Structlog: JSON when not a TTY (Docker logs), pretty-printed in terminal. Log level via `SEEKR_LOG_LEVEL` env var.

## Environment Setup

Copy `.env.example` → `.env` and `config/seekr.example.yaml` → `config/seekr.yaml` before first run.

Required env vars: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`, `SEEKR_DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

The Docker entrypoint (`scripts/start.sh`) runs `alembic upgrade head` automatically before starting the app.
