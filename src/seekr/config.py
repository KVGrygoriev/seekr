from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)


class Classification(StrEnum):
    NEW = "NEW"
    UPDATED_BY_OWNER = "UPDATED_BY_OWNER"
    REPOSTED_BY_OTHER = "REPOSTED_BY_OTHER"
    PRICE_CHANGED = "PRICE_CHANGED"
    UNCHANGED = "UNCHANGED"


class ScheduleMode(StrEnum):
    ONESHOT = "oneshot"
    INTERVAL = "interval"


class OrderKey(StrEnum):
    PRICE_PER_100M2_ASC = "price_per_100m2_asc"
    PRICE_PER_100M2_DESC = "price_per_100m2_desc"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    AREA_ASC = "area_asc"
    AREA_DESC = "area_desc"
    POSTED_AT_ASC = "posted_at_asc"
    POSTED_AT_DESC = "posted_at_desc"


class GroupKey(StrEnum):
    SEARCH = "search"
    CLASSIFICATION = "classification"
    SOURCE = "source"


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class OlxSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_delay_ms: int = Field(default=1500, ge=0, le=60_000)
    max_pages: int = Field(default=5, ge=1, le=100)
    user_agent: NonEmptyStr = "SeekrBot/0.1 (+https://github.com/)"
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    olx: OlxSourceConfig = Field(default_factory=OlxSourceConfig)


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyStr
    source: NonEmptyStr
    url: HttpUrl
    enabled: bool = True

    @field_validator("source")
    @classmethod
    def _known_source(cls, v: str) -> str:
        if v != "olx":
            raise ValueError(f"unknown source '{v}'. Supported: olx")
        return v


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: NonEmptyStr = "default"
    group_by: list[GroupKey] = Field(default_factory=lambda: [GroupKey.SEARCH, GroupKey.CLASSIFICATION])
    order_by: list[OrderKey] = Field(
        default_factory=lambda: [OrderKey.PRICE_PER_100M2_ASC, OrderKey.POSTED_AT_DESC]
    )
    include_classifications: list[Classification] = Field(
        default_factory=lambda: [
            Classification.NEW,
            Classification.UPDATED_BY_OWNER,
            Classification.REPOSTED_BY_OTHER,
            Classification.PRICE_CHANGED,
        ]
    )
    include_operator_notes: bool = True


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token_env: NonEmptyStr = "TELEGRAM_BOT_TOKEN"
    chat_ids: list[int] = Field(default_factory=list)
    parse_mode: NonEmptyStr = "HTML"
    disable_web_page_preview: bool = True
    rate_limit_per_second: float = Field(default=20.0, ge=0.5, le=30.0)

    @model_validator(mode="after")
    def _chat_ids_present(self) -> "TelegramConfig":
        env_chat = os.getenv("TELEGRAM_CHAT_ID")
        if not self.chat_ids and not env_chat:
            raise ValueError(
                "telegram.chat_ids is empty and TELEGRAM_CHAT_ID env var is not set"
            )
        return self

    def resolved_chat_ids(self) -> list[int]:
        if self.chat_ids:
            return self.chat_ids
        env_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        return [int(env_chat)] if env_chat else []

    def resolved_bot_token(self) -> str:
        token = os.getenv(self.bot_token_env, "").strip()
        if not token:
            raise RuntimeError(
                f"Telegram bot token env var '{self.bot_token_env}' is empty"
            )
        return token


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ScheduleMode = ScheduleMode.ONESHOT
    interval_minutes: int = Field(default=60, ge=1, le=24 * 60)


class SeekrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    searches: list[SearchConfig]
    report: ReportConfig = Field(default_factory=ReportConfig)
    telegram: TelegramConfig
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    @field_validator("searches")
    @classmethod
    def _non_empty_searches(cls, v: list[SearchConfig]) -> list[SearchConfig]:
        if not v:
            raise ValueError("at least one search must be configured")
        names = [s.name for s in v]
        if len(set(names)) != len(names):
            raise ValueError("search names must be unique")
        return v

    def enabled_searches(self) -> list[SearchConfig]:
        return [s for s in self.searches if s.enabled]


_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env(value: str) -> str:
    """Resolve ${VAR} and ${VAR:-default} occurrences inside a string."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_val = os.getenv(var_name)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return match.group(0)

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _walk_interpolate(node: object) -> object:
    if isinstance(node, str):
        return _interpolate_env(node)
    if isinstance(node, list):
        return [_walk_interpolate(item) for item in node]
    if isinstance(node, dict):
        return {key: _walk_interpolate(val) for key, val in node.items()}
    return node


def load_config(path: str | Path) -> SeekrConfig:
    """Load and validate a Seekr YAML config from disk.

    Supports ${ENV_VAR} and ${ENV_VAR:-default} interpolation in string values.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping, got {type(raw).__name__}")
    interpolated = _walk_interpolate(raw)
    return SeekrConfig.model_validate(interpolated)
