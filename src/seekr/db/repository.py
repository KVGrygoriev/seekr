from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from seekr.db.models import (
    Listing,
    ListingHistory,
    OperatorNote,
    ReportDispatch,
    Search,
    Source,
)


def hash_config(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Repository:
    """Aggregate of DB queries used by the rest of the app.

    All methods operate on the AsyncSession passed in at construction.
    Callers control the transaction (see `session_scope`).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- sources ----------------------------------------------------------

    async def upsert_source(self, name: str) -> Source:
        stmt = (
            pg_insert(Source)
            .values(name=name)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(Source.id)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is not None:
            return await self._source_by_id(row[0])
        existing = await self.session.execute(select(Source).where(Source.name == name))
        return existing.scalar_one()

    async def _source_by_id(self, source_id: int) -> Source:
        result = await self.session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one()

    # --- searches ---------------------------------------------------------

    async def upsert_search(
        self, *, source_id: int, name: str, url: str, enabled: bool, config_hash: str
    ) -> Search:
        stmt = (
            pg_insert(Search)
            .values(
                source_id=source_id,
                name=name,
                url=url,
                enabled=enabled,
                config_hash=config_hash,
            )
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "source_id": source_id,
                    "url": url,
                    "enabled": enabled,
                    "config_hash": config_hash,
                },
            )
            .returning(Search.id)
        )
        result = await self.session.execute(stmt)
        search_id = result.scalar_one()
        existing = await self.session.execute(select(Search).where(Search.id == search_id))
        return existing.scalar_one()

    async def mark_search_run(self, search_id: int, at: datetime) -> None:
        result = await self.session.execute(select(Search).where(Search.id == search_id))
        search = result.scalar_one()
        search.last_run_at = at

    # --- listings ---------------------------------------------------------

    async def get_listing_by_external(
        self, source_id: int, external_id: str
    ) -> Optional[Listing]:
        result = await self.session.execute(
            select(Listing).where(
                Listing.source_id == source_id,
                Listing.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_listings_by_fingerprint(
        self, source_id: int, fingerprint: str, *, exclude_id: int | None = None
    ) -> Sequence[Listing]:
        stmt = select(Listing).where(
            Listing.source_id == source_id,
            Listing.fingerprint == fingerprint,
        )
        if exclude_id is not None:
            stmt = stmt.where(Listing.id != exclude_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def insert_listing(self, listing: Listing) -> Listing:
        self.session.add(listing)
        await self.session.flush()
        return listing

    async def touch_listing_seen(self, listing: Listing, at: datetime) -> None:
        listing.last_seen_at = at

    # --- history ----------------------------------------------------------

    async def append_history(
        self,
        *,
        listing_id: int,
        captured_at: datetime,
        price: Decimal | None,
        currency: str,
        title: str,
        location: str,
        area_m2: Decimal | None,
        seller_url: str | None,
        change_kind: str,
    ) -> ListingHistory:
        entry = ListingHistory(
            listing_id=listing_id,
            captured_at=captured_at,
            price=price,
            currency=currency,
            title=title,
            location=location,
            area_m2=area_m2,
            seller_url=seller_url,
            change_kind=change_kind,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def history_for_listing(self, listing_id: int) -> Sequence[ListingHistory]:
        result = await self.session.execute(
            select(ListingHistory)
            .where(ListingHistory.listing_id == listing_id)
            .order_by(ListingHistory.captured_at)
        )
        return result.scalars().all()

    # --- dispatches -------------------------------------------------------

    async def latest_dispatch(
        self, *, search_id: int, listing_id: int
    ) -> Optional[ReportDispatch]:
        result = await self.session.execute(
            select(ReportDispatch)
            .where(
                ReportDispatch.search_id == search_id,
                ReportDispatch.listing_id == listing_id,
            )
            .order_by(ReportDispatch.dispatched_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def record_dispatch(
        self,
        *,
        search_id: int,
        listing_id: int,
        classification: str,
        price_snapshot: Decimal | None,
        at: datetime | None = None,
    ) -> ReportDispatch:
        entry = ReportDispatch(
            search_id=search_id,
            listing_id=listing_id,
            classification=classification,
            price_snapshot=price_snapshot,
            dispatched_at=at or datetime.now(timezone.utc),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    # --- operator notes ---------------------------------------------------

    async def operator_notes_for(
        self, listing_ids: Iterable[int]
    ) -> dict[int, OperatorNote]:
        ids = list(listing_ids)
        if not ids:
            return {}
        result = await self.session.execute(
            select(OperatorNote).where(OperatorNote.listing_id.in_(ids))
        )
        return {note.listing_id: note for note in result.scalars().all()}
