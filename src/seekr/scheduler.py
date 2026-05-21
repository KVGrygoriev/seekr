from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from seekr.config import ScheduleMode, SeekrConfig
from seekr.logging import get_logger
from seekr.pipeline import run_once

log = get_logger("seekr.scheduler")


async def _safe_run_once(config: SeekrConfig) -> None:
    try:
        await run_once(config)
    except Exception as exc:  # noqa: BLE001
        log.error("scheduled_run.failed", error=str(exc), exc_info=True)


async def serve_async(config: SeekrConfig) -> None:
    """Long-running serve mode. Honours config.schedule."""
    if config.schedule.mode is ScheduleMode.ONESHOT:
        log.info("serve.oneshot_run")
        await _safe_run_once(config)
        return

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _safe_run_once,
        IntervalTrigger(minutes=config.schedule.interval_minutes),
        args=(config,),
        next_run_time=None,  # we kick off an immediate run below
        id="seekr.tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("serve.scheduler_started", interval_minutes=config.schedule.interval_minutes)

    # Trigger an immediate run on startup, then let the scheduler take over.
    await _safe_run_once(config)

    stop_event = asyncio.Event()

    def _on_signal(signame: str) -> None:
        log.info("serve.signal_received", signal=signame)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal, sig.name)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        log.info("serve.scheduler_stopped")


def serve_sync(config: SeekrConfig) -> None:
    asyncio.run(serve_async(config))
