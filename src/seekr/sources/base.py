from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from seekr.config import SearchConfig
from seekr.domain.models import RawListing


@runtime_checkable
class SourceAdapter(Protocol):
    """A source of listings — OLX, future sources implement this protocol."""

    source_name: str

    def fetch_listings(self, search: SearchConfig) -> AsyncIterator[RawListing]:
        """Yield every visible listing for the given search."""
        ...
