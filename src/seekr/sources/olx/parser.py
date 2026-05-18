from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterator
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from seekr.domain.models import RawListing

OLX_BASE = "https://www.olx.ua"
SOURCE_NAME = "olx"

# Price text formats observed on OLX:
#   "35 000 $"  "35000 $"  "1 250 000 грн."  "Договірна"
_PRICE_RE = re.compile(r"([\d\s ]+)\s*([$€]|грн)", re.IGNORECASE)
# Area text: "10 сот." / "0.1 га" / "1500 м²"
_AREA_SOTKA_RE = re.compile(r"([\d.,]+)\s*(сот|сотк)", re.IGNORECASE)
_AREA_HA_RE = re.compile(r"([\d.,]+)\s*га", re.IGNORECASE)
_AREA_M2_RE = re.compile(r"([\d.,]+)\s*м²", re.IGNORECASE)
# External id embedded in a listing URL: ...-ID<digits-or-alnum>.html
_EXTERNAL_ID_RE = re.compile(r"-ID([A-Za-z0-9]+)\.html", re.IGNORECASE)

_CURRENCY_SYMBOL_TO_CODE = {"$": "USD", "€": "EUR", "грн": "UAH"}


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return (node.text(strip=True) or "").strip()


def _parse_decimal(raw: str) -> Decimal | None:
    if not raw:
        return None
    cleaned = raw.replace(" ", " ").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_price(text: str) -> tuple[Decimal | None, str]:
    if not text:
        return None, "USD"
    m = _PRICE_RE.search(text)
    if not m:
        return None, "USD"
    amount = _parse_decimal(m.group(1))
    currency = _CURRENCY_SYMBOL_TO_CODE.get(m.group(2).lower(), "USD")
    return amount, currency


def _parse_area_m2(text: str) -> Decimal | None:
    """Return area in square metres. 1 сотка = 100 m². 1 га = 10 000 m²."""
    if not text:
        return None
    if (m := _AREA_HA_RE.search(text)) is not None:
        val = _parse_decimal(m.group(1))
        return val * Decimal(10_000) if val is not None else None
    if (m := _AREA_SOTKA_RE.search(text)) is not None:
        val = _parse_decimal(m.group(1))
        return val * Decimal(100) if val is not None else None
    if (m := _AREA_M2_RE.search(text)) is not None:
        return _parse_decimal(m.group(1))
    return None


def _parse_external_id(url: str) -> str | None:
    m = _EXTERNAL_ID_RE.search(url)
    return m.group(1) if m else None


_UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}


def _parse_posted_at(text: str, now: datetime) -> datetime | None:
    """OLX uses 'Сьогодні о HH:MM' / 'Вчора о HH:MM' / '12 травня 2026 р.'"""
    if not text:
        return None
    text = text.strip().lower()
    time_match = re.search(r"(\d{1,2}):(\d{2})", text)
    hour, minute = (int(time_match.group(1)), int(time_match.group(2))) if time_match else (0, 0)
    if "сьогодні" in text or "today" in text:
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "вчора" in text or "yesterday" in text:
        return (now - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    m = re.search(r"(\d{1,2})\s+([Ѐ-ӿ]+)\s*(\d{4})?", text)
    if m:
        day = int(m.group(1))
        month = _UA_MONTHS.get(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        if month:
            try:
                return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _attr(node: Node | None, name: str) -> str | None:
    if node is None:
        return None
    return node.attributes.get(name)


def parse_search_page(html: str, *, base_url: str = OLX_BASE, now: datetime | None = None) -> Iterator[RawListing]:
    """Yield RawListing objects extracted from an OLX search-results page."""
    now = now or datetime.now(timezone.utc)
    tree = HTMLParser(html)
    cards = tree.css('[data-cy="l-card"]')
    if not cards:
        cards = tree.css("[data-testid='l-card']") or tree.css("div.css-1apmciz")

    for card in cards:
        link = card.css_first("a[href]")
        href = _attr(link, "href") or ""
        if not href:
            continue
        url = href if href.startswith("http") else urljoin(base_url, href)
        external_id = _parse_external_id(url) or _attr(card, "id") or url

        title_node = card.css_first('[data-cy="ad-card-title"] h6') or card.css_first("h6") or link
        title = _text(title_node)

        price_node = card.css_first('[data-testid="ad-price"]') or card.css_first("p[data-testid='ad-price']")
        price, currency = _parse_price(_text(price_node))

        location_date_node = (
            card.css_first('[data-testid="location-date"]')
            or card.css_first("p[data-testid='location-date']")
        )
        location_date_text = _text(location_date_node)
        location, _, posted_text = location_date_text.partition(" - ")
        location = location.strip()
        posted_at = _parse_posted_at(posted_text, now)

        area_text_node = (
            card.css_first('[data-testid="ad-attributes"]')
            or card.css_first("[data-cy='ad-attributes']")
        )
        area_text = _text(area_text_node) or title
        area_m2 = _parse_area_m2(area_text)

        if not title:
            continue

        yield RawListing(
            source=SOURCE_NAME,
            external_id=external_id,
            url=url,
            title=title,
            location=location,
            area_m2=area_m2,
            price=price,
            currency=currency or "USD",
            posted_at=posted_at,
        )


def find_next_page_url(html: str, *, base_url: str = OLX_BASE) -> str | None:
    """Return absolute URL of the next pagination page if present."""
    tree = HTMLParser(html)
    candidate = (
        tree.css_first('[data-testid="pagination-forward"]')
        or tree.css_first('a[data-cy="pagination-forward"]')
    )
    href = _attr(candidate, "href")
    if not href:
        return None
    return href if href.startswith("http") else urljoin(base_url, href)
