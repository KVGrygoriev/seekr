from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from seekr.config import Classification, SearchConfig
from seekr.db.models import Listing
from seekr.db.repository import Repository
from seekr.domain.fingerprint import fingerprint as compute_fingerprint
from seekr.domain.models import ClassifiedListing, RawListing
from seekr.logging import get_logger

log = get_logger("seekr.diff")


def _price_per_100m2(price: Decimal | None, area_m2: Decimal | None) -> Decimal | None:
    if price is None or area_m2 is None or area_m2 == 0:
        return None
    return (price * Decimal(100) / area_m2).quantize(Decimal("0.01"))



class DiffEngine:
    """Classify fresh RawListings against repo state and persist deltas."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def classify_and_persist(
        self,
        *,
        source_id: int,
        search: SearchConfig,
        search_id: int,
        raw_listings: list[RawListing],
        now: datetime | None = None,
    ) -> list[ClassifiedListing]:
        now = now or datetime.now(timezone.utc)
        results: list[ClassifiedListing] = []

        current_external_ids = {r.external_id for r in raw_listings}

        for raw in raw_listings:
            fp = compute_fingerprint(raw.title, raw.location, raw.area_m2)
            existing = await self.repo.get_listing_by_external(source_id, raw.external_id)

            if existing is not None:
                classification, previous_url, changed_fields = await self._classify_existing(
                    existing=existing, raw=raw, fp=fp, captured_at=now
                )
                listing_id = existing.id
            else:
                classification, listing_id, previous_url = await self._handle_new(
                    source_id=source_id,
                    raw=raw,
                    fp=fp,
                    captured_at=now,
                    current_external_ids=current_external_ids,
                )
                changed_fields: frozenset[str] = frozenset()

            price_history = await self._price_history(listing_id)

            results.append(
                ClassifiedListing(
                    raw=raw,
                    fingerprint=fp,
                    price_per_100m2=_price_per_100m2(raw.price, raw.area_m2),
                    classification=classification,
                    listing_id=listing_id,
                    previous_url=previous_url,
                    price_history=price_history,
                    search_id=search_id,
                    search_name=search.name,
                    changed_fields=changed_fields,
                )
            )

        await self.repo.mark_search_run(search_id, now)
        return results

    async def _classify_existing(
        self,
        *,
        existing: Listing,
        raw: RawListing,
        fp: str,
        captured_at: datetime,
    ) -> tuple[Classification, str | None, frozenset[str]]:
        changed: set[str] = set()
        if raw.price is not None and existing.current_price != raw.price:
            changed.add("price")
        if existing.title != raw.title:
            changed.add("title")
        if existing.location != raw.location:
            changed.add("location")
        if existing.area_m2 != raw.area_m2:
            changed.add("area_m2")
        if existing.current_url != raw.url:
            changed.add("url")

        price_changed = "price" in changed
        content_changed = bool(changed - {"price"})

        previous_url = existing.current_url

        if price_changed:
            classification = Classification.PRICE_CHANGED
        elif content_changed:
            classification = Classification.UPDATED_BY_OWNER
        else:
            classification = Classification.UNCHANGED

        existing.current_url = raw.url
        existing.title = raw.title
        existing.location = raw.location
        existing.area_m2 = raw.area_m2
        existing.fingerprint = fp
        if raw.price is not None:
            existing.current_price = raw.price
        existing.currency = raw.currency or existing.currency
        existing.current_price_per_100m2 = _price_per_100m2(
            existing.current_price, existing.area_m2
        )
        if raw.posted_at is not None:
            existing.posted_at = existing.posted_at or raw.posted_at
        existing.last_seen_at = captured_at
        existing.updated_at_source = captured_at if content_changed or price_changed else existing.updated_at_source

        if classification is not Classification.UNCHANGED:
            await self.repo.append_history(
                listing_id=existing.id,
                captured_at=captured_at,
                price=existing.current_price,
                currency=existing.currency,
                title=raw.title,
                location=raw.location,
                area_m2=raw.area_m2,
                seller_url=raw.url,
                change_kind=classification.value,
            )
        return classification, previous_url, frozenset(changed)

    async def _handle_new(
        self,
        *,
        source_id: int,
        raw: RawListing,
        fp: str,
        captured_at: datetime,
        current_external_ids: set[str],
    ) -> tuple[Classification, int, str | None]:
        repost_origin = None
        siblings = await self.repo.find_listings_by_fingerprint(source_id, fp)
        # A sibling still present in the current batch means two concurrent listings
        # with identical details — not a repost.
        gone_siblings = [s for s in siblings if s.external_id not in current_external_ids]
        if gone_siblings:
            repost_origin = gone_siblings[0].current_url
            classification = Classification.REPOSTED_BY_OTHER
        else:
            classification = Classification.NEW

        listing = Listing(
            source_id=source_id,
            external_id=raw.external_id,
            current_url=raw.url,
            title=raw.title,
            location=raw.location,
            area_m2=raw.area_m2,
            current_price=raw.price,
            currency=raw.currency or "USD",
            current_price_per_100m2=_price_per_100m2(raw.price, raw.area_m2),
            fingerprint=fp,
            status="active",
            posted_at=raw.posted_at,
            updated_at_source=captured_at,
            first_seen_at=captured_at,
            last_seen_at=captured_at,
        )
        await self.repo.insert_listing(listing)

        await self.repo.append_history(
            listing_id=listing.id,
            captured_at=captured_at,
            price=listing.current_price,
            currency=listing.currency,
            title=raw.title,
            location=raw.location,
            area_m2=raw.area_m2,
            seller_url=raw.url,
            change_kind=classification.value,
        )
        return classification, listing.id, repost_origin

    async def _price_history(self, listing_id: int) -> list[tuple[datetime, Decimal, str]]:
        entries = await self.repo.history_for_listing(listing_id)
        return [
            (h.captured_at, h.price, h.currency)
            for h in entries
            if h.price is not None
        ]


def should_dispatch(
    *,
    latest_classification: str | None,
    latest_price_snapshot: Decimal | None,
    current_classification: Classification,
    current_price_snapshot: Decimal | None,
) -> bool:
    """Decide whether a classified listing should be (re-)sent.

    A listing is dispatched if it has never been dispatched for this search,
    OR the classification differs, OR the price snapshot differs.
    UNCHANGED is never dispatched.
    """
    if current_classification is Classification.UNCHANGED:
        return False
    if latest_classification is None:
        return True
    if latest_classification != current_classification.value:
        return True
    return latest_price_snapshot != current_price_snapshot
