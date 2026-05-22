# Seekr

OLX.ua scraper that detects new / updated / reposted listings, tracks price
history, and reports deltas to a Telegram bot. Backend only, runs in Docker
with Postgres alongside.

## Features

- One or more named searches per config (any OLX category/filter URL).
- Listings are uniformly fetched in USD (`?currency=USD`).
- Per-listing classification:
  - `NEW` — never seen before.
  - `UPDATED_BY_OWNER` — same OLX id, title/location/area changed.
  - `REPOSTED_BY_OTHER` — different OLX id but same normalized
    `(title, location, area)` fingerprint as a prior listing.
  - `PRICE_CHANGED` — price moved; the report carries the full history.
- Reports are sent to Telegram as one message per listing, grouped by the
  fields you choose (`search`, `classification`, `source`) and ordered by
  fields you choose (`price_per_100m2`, `price`, `area`, `posted_at`).
- Already-sent listings are suppressed via a `report_dispatches` ledger:
  a listing is re-sent only if its classification or its price changes.
- Operators can flag listings in Postgres (`operator_notes` table); when a
  flagged listing reappears in a future report, the note renders inline.

## Quickstart

1. Copy the example config and edit it for your searches.

   ```bash
   cp .env.example .env
   cp config/seekr.example.yaml config/seekr.yaml
   ```

   In `.env` set `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`
   (from [@BotFather](https://t.me/BotFather)) and `TELEGRAM_CHAT_ID`
   (from [@userinfobot](https://t.me/userinfobot)).

2. Bring up Postgres and apply migrations.

   ```bash
   make up
   make migrate
   ```

3. Run once or run as a daemon.

   ```bash
   make run-once   # process now and exit
   make serve      # long-running, ticks per schedule.interval_minutes
   ```

   `serve` mode automatically applies pending migrations on start.

## Configuration

Edit `config/seekr.yaml`. Keys are validated by pydantic — unknown keys are
errors. String values may use `${ENV_VAR}` or `${ENV_VAR:-default}` interpolation.

```yaml
sources:
  olx:
    request_delay_ms: 1500
    max_pages: 5

searches:
  - name: "Land — Sofiivska Borshchahivka"
    source: olx
    url: "https://www.olx.ua/uk/nedvizhimost/zemlya/prodazha-zemli/sofievskaya-borschagovka/"
    enabled: true

report:
  group_by: [search, classification]
  order_by: [price_per_100m2_asc, posted_at_desc]
  include_classifications: [NEW, UPDATED_BY_OWNER, REPOSTED_BY_OTHER, PRICE_CHANGED]
  include_operator_notes: true

telegram:
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_ids: []          # leave empty to fall back to $TELEGRAM_CHAT_ID
  parse_mode: HTML

schedule:
  mode: interval        # or "oneshot"
  interval_minutes: 60
```

Validate the file without running anything:

```bash
docker compose run --rm app seekr check-config
```

## Operator notes

To flag a listing, write to the `operator_notes` table from any SQL client.

```sql
INSERT INTO operator_notes (listing_id, status, operator, comment)
VALUES (
  (SELECT id FROM listings WHERE current_url LIKE '%IDabc12%'),
  'contacted',
  'kostia',
  'Owner asked to call back next week'
)
ON CONFLICT (listing_id) DO UPDATE SET
  status = EXCLUDED.status,
  operator = EXCLUDED.operator,
  comment = EXCLUDED.comment,
  updated_at = now();
```

The next report that mentions this listing will render the status and comment
inline.

## Inspecting state

```bash
make psql                                     # open psql in the running compose
\dt                                           # list tables
SELECT current_url, current_price, fingerprint FROM listings ORDER BY last_seen_at DESC LIMIT 20;
SELECT listing_id, change_kind, price, captured_at FROM listing_history ORDER BY captured_at DESC LIMIT 50;
SELECT * FROM report_dispatches ORDER BY dispatched_at DESC LIMIT 20;
```

## Make targets

| Target          | Description                                                         |
|-----------------|---------------------------------------------------------------------|
| `make up`       | Start postgres in the background.                                    |
| `make down`     | Stop and remove the compose stack.                                   |
| `make build`    | Rebuild the app image.                                               |
| `make migrate`  | `alembic upgrade head` against the running postgres.                 |
| `make run-once` | One-shot pipeline run via the app image.                             |
| `make serve`    | Foreground long-running daemon (interval scheduler).                 |
| `make psql`     | Open psql against the running postgres.                              |
| `make lint`     | Run ruff against `src/`.                                             |

## Project layout

```
src/seekr/
├── cli.py           # Typer CLI
├── config.py        # Pydantic config + YAML loader
├── logging.py       # structlog
├── pipeline.py      # fetch → diff → dispatch wiring
├── scheduler.py     # APScheduler glue for `serve`
├── sources/         # SourceAdapter protocol + OlxAdapter
├── domain/          # RawListing, ClassifiedListing, fingerprint
├── diff/            # DiffEngine + should_dispatch
├── db/              # SQLAlchemy 2.0 async models + repository
├── report/          # ReportBuilder + Jinja templates
└── telegram/        # Bot API client
```

Adding a new source: implement the `SourceAdapter` protocol in
`src/seekr/sources/<name>/adapter.py`, register it in `pipeline._adapter_for`,
and add the new source name to `SearchConfig`'s allow-list.

## License

MIT
