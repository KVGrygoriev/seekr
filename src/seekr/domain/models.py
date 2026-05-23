from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from seekr.config import Classification


@dataclass(frozen=True, slots=True)
class RawListing:
    """A listing as extracted from a source's HTML — pre-domain, pre-fingerprint."""

    source: str
    external_id: str
    url: str
    title: str
    location: str
    area_m2: Optional[Decimal]
    price: Optional[Decimal]
    currency: str
    posted_at: Optional[datetime]


@dataclass(slots=True)
class ClassifiedListing:
    """A `RawListing` paired with its classification and supporting context."""

    raw: RawListing
    fingerprint: str
    price_per_100m2: Optional[Decimal]
    classification: Classification
    listing_id: int
    previous_url: Optional[str] = None
    price_history: list[tuple[datetime, Decimal, str]] = field(default_factory=list)
    search_id: int = 0
    search_name: str = ""
    changed_fields: frozenset[str] = field(default_factory=frozenset)
