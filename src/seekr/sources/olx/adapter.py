from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from seekr.config import OlxSourceConfig, SearchConfig
from seekr.domain.models import RawListing
from seekr.logging import get_logger
from seekr.sources.olx.parser import (
    OLX_BASE,
    SOURCE_NAME,
    find_next_page_url,
    parse_search_page,
)

log = get_logger("seekr.sources.olx")


def _with_usd_currency(url: str) -> str:
    """Inject ?currency=USD (replacing any existing one) into the URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["currency"] = ["USD"]
    new_query = urlencode([(k, v) for k, vs in qs.items() for v in vs])
    return urlunparse(parsed._replace(query=new_query))


class OlxAdapter:
    source_name = SOURCE_NAME

    def __init__(self, config: OlxSourceConfig) -> None:
        self._config = config

    async def fetch_listings(self, search: SearchConfig) -> AsyncIterator[RawListing]:
        async with httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            headers={
                "User-Agent": self._config.user_agent,
                "Accept-Language": "uk,en;q=0.8",
            },
            follow_redirects=True,
            base_url=OLX_BASE,
        ) as client:
            url: str | None = _with_usd_currency(str(search.url))
            pages_fetched = 0
            seen_external_ids: set[str] = set()
            while url is not None and pages_fetched < self._config.max_pages:
                html = await self._fetch_one(client, url, search_name=search.name)
                pages_fetched += 1
                page_yielded = 0
                for raw in parse_search_page(html):
                    if raw.external_id in seen_external_ids:
                        continue
                    seen_external_ids.add(raw.external_id)
                    page_yielded += 1
                    yield raw
                log.info(
                    "olx.page_parsed",
                    search=search.name,
                    page=pages_fetched,
                    listings=page_yielded,
                )
                next_url = find_next_page_url(html)
                if next_url is None:
                    break
                url = _with_usd_currency(next_url)
                await asyncio.sleep(self._config.request_delay_ms / 1000)

    async def _fetch_one(self, client: httpx.AsyncClient, url: str, *, search_name: str) -> str:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                log.debug("olx.fetch", search=search_name, url=url)
                response = await client.get(url)
                response.raise_for_status()
                return response.text
        raise RuntimeError("unreachable")
