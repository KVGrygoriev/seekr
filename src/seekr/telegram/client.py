from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from seekr.config import TelegramConfig
from seekr.logging import get_logger
from seekr.report.builder import HeaderMessage, ListingMessage, Message

log = get_logger("seekr.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096


class TelegramSendError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        self._token = config.resolved_bot_token()
        self._chat_ids = config.resolved_chat_ids()
        # Translate per-second budget into a min delay per request.
        self._min_delay = 1.0 / max(self._config.rate_limit_per_second, 0.1)

    async def send_messages(self, messages: Sequence[Message]) -> list[ListingMessage]:
        """Send each message to every configured chat in order.

        Returns the subset of ListingMessage that were dispatched successfully —
        the caller persists them to report_dispatches.
        """
        if not messages:
            return []

        dispatched: list[ListingMessage] = []
        async with httpx.AsyncClient(
            base_url=f"{TELEGRAM_API_BASE}/bot{self._token}",
            timeout=20.0,
        ) as client:
            for msg in messages:
                text = msg.text if len(msg.text) <= MAX_MESSAGE_LENGTH else (
                    msg.text[: MAX_MESSAGE_LENGTH - 3] + "..."
                )
                for chat_id in self._chat_ids:
                    await self._send_one(client, chat_id=chat_id, text=text)
                    await asyncio.sleep(self._min_delay)
                if isinstance(msg, ListingMessage):
                    dispatched.append(msg)
                elif isinstance(msg, HeaderMessage):
                    log.debug("telegram.header_sent", text=msg.text)
        return dispatched

    async def send_text(self, text: str) -> None:
        """Send a plain text message to every configured chat."""
        async with httpx.AsyncClient(
            base_url=f"{TELEGRAM_API_BASE}/bot{self._token}",
            timeout=20.0,
        ) as client:
            for chat_id in self._chat_ids:
                await self._send_one(client, chat_id=chat_id, text=text)
                await asyncio.sleep(self._min_delay)

    async def _send_one(self, client: httpx.AsyncClient, *, chat_id: int, text: str) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": self._config.parse_mode,
            "disable_web_page_preview": self._config.disable_web_page_preview,
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
            retry=retry_if_exception_type((httpx.TransportError, TelegramSendError)),
            reraise=True,
        ):
            with attempt:
                response = await client.post("/sendMessage", json=payload)
                if response.status_code == 429:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 1)
                    log.warning("telegram.rate_limited", retry_after=retry_after)
                    await asyncio.sleep(float(retry_after))
                    raise TelegramSendError("rate limited")
                if response.status_code >= 400:
                    log.error(
                        "telegram.send_failed",
                        status=response.status_code,
                        body=response.text[:500],
                        chat_id=chat_id,
                    )
                    if 500 <= response.status_code < 600:
                        raise TelegramSendError(f"telegram {response.status_code}")
                    raise TelegramSendError(
                        f"telegram refused: {response.status_code} {response.text[:200]}"
                    )
