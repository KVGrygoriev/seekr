from __future__ import annotations

import re
import unicodedata
from decimal import Decimal


_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_text(value: str) -> str:
    """Lower-case, strip diacritics, collapse whitespace, drop punctuation."""
    if not value:
        return ""
    nfkd = unicodedata.normalize("NFKD", value)
    no_diacritics = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    folded = no_diacritics.casefold()
    no_punct = _PUNCT.sub(" ", folded)
    return _WHITESPACE.sub(" ", no_punct).strip()


def fingerprint(title: str, location: str, area_m2: Decimal | None) -> str:
    """Stable identity for a listing across reposts.

    Same title + location + area ⇒ same fingerprint, even if external_id changes.
    """
    area_part = f"{area_m2:.2f}" if area_m2 is not None else "?"
    return f"{normalize_text(title)}|{normalize_text(location)}|{area_part}"
