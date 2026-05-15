from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from seekr.logging import configure_logging, get_logger

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
    config_path = _resolve_config_path(config)
    log.info("run_once.start", config_path=str(config_path))
    log.warning("run_once.not_implemented", note="wired up in a later commit")


@app.command("serve")
def serve(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to YAML config (overrides SEEKR_CONFIG_PATH)."),
    ] = None,
) -> None:
    """Run continuously, executing the pipeline on the configured schedule."""
    config_path = _resolve_config_path(config)
    log.info("serve.start", config_path=str(config_path))
    log.warning("serve.not_implemented", note="wired up in a later commit")


if __name__ == "__main__":
    app()
