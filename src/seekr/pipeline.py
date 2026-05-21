from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from seekr.config import SearchConfig, SeekrConfig
from seekr.db.engine import create_engine, session_factory, session_scope
from seekr.db.repository import Repository, hash_config
from seekr.diff.engine import DiffEngine, should_dispatch
from seekr.domain.models import RawListing
from seekr.logging import get_logger
from seekr.report.builder import ListingMessage, Message, ReportBuilder
from seekr.sources.olx.adapter import OlxAdapter
from seekr.telegram.client import TelegramClient

log = get_logger("seekr.pipeline")


def _adapter_for(source: str, config: SeekrConfig):
    if source == "olx":
        return OlxAdapter(config.sources.olx)
    raise ValueError(f"unknown source '{source}'")


async def _collect(raw_iter) -> list[RawListing]:
    out: list[RawListing] = []
    async for raw in raw_iter:
        out.append(raw)
    return out


async def _filter_for_dispatch(
    repo: Repository, search_id: int, messages: list[Message]
) -> list[Message]:
    """Drop ListingMessages whose (search, listing, classification, price) was already dispatched."""
    filtered: list[Message] = []
    for msg in messages:
        if not isinstance(msg, ListingMessage):
            filtered.append(msg)
            continue
        latest = await repo.latest_dispatch(search_id=search_id, listing_id=msg.listing_id)
        if should_dispatch(
            latest_classification=latest.classification if latest else None,
            latest_price_snapshot=latest.price_snapshot if latest else None,
            current_classification=msg.classification,
            current_price_snapshot=msg.price_snapshot,
        ):
            filtered.append(msg)
    return filtered


def _drop_empty_headers(messages: list[Message]) -> list[Message]:
    """Remove header messages whose group ended up with no listings."""
    if not messages:
        return messages
    group_has_listings = {
        msg.group_key for msg in messages if isinstance(msg, ListingMessage)
    }
    return [
        msg
        for msg in messages
        if isinstance(msg, ListingMessage) or msg.group_key in group_has_listings
    ]


async def run_once(config: SeekrConfig) -> None:
    """Single execution of the full pipeline."""
    engine = create_engine()
    factory = session_factory(engine)
    try:
        async with session_scope(factory) as session:
            repo = Repository(session)
            source = await repo.upsert_source("olx")
            log.info("pipeline.start", source=source.name, searches=len(config.enabled_searches()))

        builder = ReportBuilder(config.report)
        telegram = TelegramClient(config.telegram)

        for search_cfg in config.enabled_searches():
            await _process_search(
                config=config,
                search_cfg=search_cfg,
                factory=factory,
                builder=builder,
                telegram=telegram,
            )
    finally:
        await engine.dispose()


async def _process_search(
    *,
    config: SeekrConfig,
    search_cfg: SearchConfig,
    factory,
    builder: ReportBuilder,
    telegram: TelegramClient,
) -> None:
    now = datetime.now(timezone.utc)
    adapter = _adapter_for(search_cfg.source, config)
    log.info("search.start", search=search_cfg.name, url=str(search_cfg.url))
    raws = await _collect(adapter.fetch_listings(search_cfg))
    log.info("search.fetched", search=search_cfg.name, count=len(raws))

    async with session_scope(factory) as session:
        repo = Repository(session)
        source = await repo.upsert_source(search_cfg.source)
        cfg_hash = hash_config(search_cfg.name, str(search_cfg.url), search_cfg.source)
        search = await repo.upsert_search(
            source_id=source.id,
            name=search_cfg.name,
            url=str(search_cfg.url),
            enabled=search_cfg.enabled,
            config_hash=cfg_hash,
        )

        diff = DiffEngine(repo)
        classified = await diff.classify_and_persist(
            source_id=source.id,
            search=search_cfg,
            search_id=search.id,
            raw_listings=raws,
            now=now,
        )
        listing_ids = [c.listing_id for c in classified]
        notes = await repo.operator_notes_for(listing_ids)

        messages = builder.build(classified, operator_notes=notes)
        messages = _drop_empty_headers(
            await _filter_for_dispatch(repo, search.id, messages)
        )

        if not messages:
            log.info("search.nothing_to_send", search=search_cfg.name)
            return

        dispatched = await telegram.send_messages(messages)
        for msg in dispatched:
            await repo.record_dispatch(
                search_id=search.id,
                listing_id=msg.listing_id,
                classification=msg.classification.value,
                price_snapshot=msg.price_snapshot,
                at=datetime.now(timezone.utc),
            )
        log.info(
            "search.dispatched",
            search=search_cfg.name,
            listings_sent=len(dispatched),
            messages_total=len(messages),
        )


def run_once_sync(config: SeekrConfig) -> None:
    asyncio.run(run_once(config))
