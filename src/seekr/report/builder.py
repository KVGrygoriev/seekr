from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from seekr.config import Classification, GroupKey, OrderKey, ReportConfig
from seekr.db.models import OperatorNote
from seekr.domain.models import ClassifiedListing

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(slots=True, frozen=True)
class HeaderMessage:
    text: str
    group_key: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ListingMessage:
    text: str
    listing_id: int
    classification: Classification
    price_snapshot: Decimal | None
    group_key: tuple[str, ...]


Message = HeaderMessage | ListingMessage


def _order_value(item: ClassifiedListing, key: OrderKey):
    raw = item.raw
    none_sentinel_low = (1, Decimal(0))
    none_sentinel_high = (1, Decimal(0))
    # Sort with NULLs last regardless of direction by carrying a (has_value, value) tuple.
    if key in (OrderKey.PRICE_PER_100M2_ASC, OrderKey.PRICE_PER_100M2_DESC):
        v = item.price_per_100m2
    elif key in (OrderKey.PRICE_ASC, OrderKey.PRICE_DESC):
        v = raw.price
    elif key in (OrderKey.AREA_ASC, OrderKey.AREA_DESC):
        v = raw.area_m2
    elif key in (OrderKey.POSTED_AT_ASC, OrderKey.POSTED_AT_DESC):
        v = raw.posted_at
    else:
        v = None
    if v is None:
        return (1, None)
    return (0, v)


def _is_desc(key: OrderKey) -> bool:
    return key.value.endswith("_desc")


def _sorted(items: list[ClassifiedListing], order_by: list[OrderKey]) -> list[ClassifiedListing]:
    if not order_by:
        return items
    out = items
    for key in reversed(order_by):
        out = sorted(out, key=lambda it: _order_value(it, key), reverse=_is_desc(key))
    return out


def _group_value(item: ClassifiedListing, key: GroupKey) -> str:
    if key is GroupKey.SEARCH:
        return item.search_name
    if key is GroupKey.CLASSIFICATION:
        return item.classification.value
    if key is GroupKey.SOURCE:
        return item.raw.source
    return ""


_CLASSIFICATION_BADGE = {
    Classification.NEW: "🟢",
    Classification.UPDATED_BY_OWNER: "🟡",
    Classification.REPOSTED_BY_OTHER: "♻ REPOSTED",
    Classification.PRICE_CHANGED: "💱 PRICE CHANGED",
}


def _header_text(group_keys: tuple[str, ...], group_by: list[GroupKey], count: int) -> str:
    parts = []
    for k_value, k_name in zip(group_keys, group_by, strict=True):
        if k_name is GroupKey.CLASSIFICATION:
            badge = _CLASSIFICATION_BADGE.get(Classification(k_value), k_value)
            parts.append(badge)
        else:
            parts.append(k_value)
    return f"🏞 <b>{'  ·  '.join(parts)}</b>  ({count})"


class ReportBuilder:
    def __init__(self, config: ReportConfig) -> None:
        self._config = config
        self._env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
            trim_blocks=False,
            lstrip_blocks=False,
            keep_trailing_newline=False,
        )
        template_name = f"{config.template}.j2"
        self._template = self._env.get_template(template_name)

    def build(
        self,
        listings: Iterable[ClassifiedListing],
        *,
        operator_notes: dict[int, OperatorNote] | None = None,
    ) -> list[Message]:
        included = [
            it for it in listings if it.classification in self._config.include_classifications
        ]
        if not included:
            return []

        notes = operator_notes or {}

        ordered = _sorted(included, self._config.order_by)
        # Stable grouping per `group_by` order.
        group_by = self._config.group_by
        groups: dict[tuple[str, ...], list[ClassifiedListing]] = {}
        order_of_groups: list[tuple[str, ...]] = []
        for item in ordered:
            key = tuple(_group_value(item, k) for k in group_by)
            if key not in groups:
                groups[key] = []
                order_of_groups.append(key)
            groups[key].append(item)

        messages: list[Message] = []
        for key in order_of_groups:
            members = groups[key]
            messages.append(HeaderMessage(text=_header_text(key, group_by, len(members)), group_key=key))
            for item in members:
                messages.append(self._render_listing(item, key, notes.get(item.listing_id)))
        return messages

    def _render_listing(
        self,
        item: ClassifiedListing,
        group_key: tuple[str, ...],
        note: OperatorNote | None,
    ) -> ListingMessage:
        raw = item.raw
        area_sotki = (raw.area_m2 / Decimal(100)).quantize(Decimal("0.01")) if raw.area_m2 else None
        text = self._template.render(
            classification=item.classification.value,
            title=raw.title,
            url=raw.url,
            location=raw.location,
            area_m2=raw.area_m2,
            area_sotki=area_sotki,
            price=raw.price,
            currency=raw.currency,
            price_per_100m2=item.price_per_100m2,
            posted_at=raw.posted_at,
            previous_url=item.previous_url,
            price_history=item.price_history if self._config.include_classifications else [],
            operator_status=(note.status if note else None),
            operator_comment=(
                note.comment if (note and self._config.include_operator_notes) else None
            ),
            changed_fields=item.changed_fields,
        )
        return ListingMessage(
            text=text.strip(),
            listing_id=item.listing_id,
            classification=item.classification,
            price_snapshot=raw.price,
            group_key=group_key,
        )
