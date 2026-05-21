from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from seekr.config import SeekrConfig, load_config
from seekr.logging import configure_logging, get_logger
from seekr.pipeline import run_once_sync
from seekr.scheduler import serve_sync

app = typer.Typer(
    name="seekr",
    help="OLX scraper that reports new/updated listings via Telegram.",
    no_args_is_help=True,
    add_completion=False,
)

log = get_logger("seekr.cli")


def _resolve_config_path(config: Path | None) -> Path:
    if config is not None:
        return config
    env_path = os.getenv("SEEKR_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return Path("config/seekr.yaml")


def _load(config: Path | None) -> tuple[Path, SeekrConfig]:
    config_path = _resolve_config_path(config)
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        log.error("config.missing", path=str(config_path), error=str(exc))
        raise typer.Exit(code=2) from exc
    except ValidationError as exc:
        log.error("config.invalid", path=str(config_path), errors=exc.errors())
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        log.error("config.invalid", path=str(config_path), error=str(exc))
        raise typer.Exit(code=2) from exc
    log.info(
        "config.loaded",
        path=str(config_path),
        searches=len(cfg.searches),
        enabled=len(cfg.enabled_searches()),
    )
    return config_path, cfg


@app.callback()
def _root() -> None:
    configure_logging()


@app.command("run-once")
def run_once(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to YAML config (overrides SEEKR_CONFIG_PATH)."),
    ] = None,
) -> None:
    """Fetch, classify, report once and exit."""
    _, cfg = _load(config)
    log.info("run_once.start", searches=[s.name for s in cfg.enabled_searches()])
    run_once_sync(cfg)
    log.info("run_once.done")


@app.command("serve")
def serve(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to YAML config (overrides SEEKR_CONFIG_PATH)."),
    ] = None,
) -> None:
    """Run continuously, executing the pipeline on the configured schedule."""
    _, cfg = _load(config)
    log.info(
        "serve.start",
        mode=cfg.schedule.mode.value,
        interval_minutes=cfg.schedule.interval_minutes,
    )
    serve_sync(cfg)


@app.command("check-config")
def check_config(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to YAML config (overrides SEEKR_CONFIG_PATH)."),
    ] = None,
) -> None:
    """Validate the config file and print a summary."""
    path, cfg = _load(config)
    sys.stdout.write(
        f"OK · {path} · {len(cfg.searches)} searches "
        f"({len(cfg.enabled_searches())} enabled) · "
        f"schedule={cfg.schedule.mode.value}\n"
    )


if __name__ == "__main__":
    app()
