"""Helpers shared by the import management commands."""

import re
from datetime import date, datetime
from difflib import SequenceMatcher

from .models import Property

TYPE_PREFIX_RE = re.compile(r"^\s*(townhouse|condo|house)\s*:\s*", re.IGNORECASE)
ADDRESS_KEY_RE = re.compile(r"\b(\d{2,})\s+([A-Za-z]+)")


def normalize_header(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def split_type_prefix(raw: str) -> tuple[str, str]:
    """Return (property_type, one-line address) for a spreadsheet address cell."""
    text = str(raw or "").strip()
    prop_type = Property.PropertyType.HOUSE
    match = TYPE_PREFIX_RE.match(text)
    if match:
        prop_type = match.group(1).lower()
        text = text[match.end():]
    return prop_type, one_line_address(text)


def one_line_address(text: str) -> str:
    """Join multi-line addresses: line breaks become ", " (unless the line already ends with a comma)."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(text).splitlines()]
    lines = [line for line in lines if line]
    result = ""
    for line in lines:
        if not result:
            result = line
        elif result.endswith(","):
            result += " " + line
        else:
            result += ", " + line
    return re.sub(r"\s+", " ", result).strip()


def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def address_key(text: str) -> tuple[str, str] | None:
    """(house number, first word of street name), lower-case, e.g. ("2101", "maple")."""
    _, address = split_type_prefix(text)
    match = ADDRESS_KEY_RE.search(address.replace("_", " "))
    if not match:
        return None
    return match.group(1), match.group(2).lower()


def match_property(folder_name: str, properties: list[Property]) -> tuple[Property | None, str]:
    """Match a photo folder name to a property.

    Primary rule: same house number and same first street word (case-insensitive).
    Fallback for typos: same house number and a similar first street word, if unique.
    Returns (property or None, "exact" | "fuzzy" | reason).
    """
    text = folder_name.replace("_", " ")
    numbers = set(re.findall(r"\d{2,}", text))
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
    if not numbers or not words:
        return None, "no house number / street in folder name"
    keyed = [(p, address_key(p.address)) for p in properties]
    keyed = [(p, k) for p, k in keyed if k and k[0] in numbers]

    # Primary rule: "<number> <word>" at the start of the folder name, as in the spreadsheet.
    key = address_key(text)
    exact = [p for p, k in keyed if key and k == key]
    if len(exact) == 1:
        return exact[0], "exact"
    # Same rule, but the number and street word may appear in any order / position.
    exact = [p for p, k in keyed if k[1] in words]
    if len(exact) == 1:
        return exact[0], "exact"
    if len(exact) > 1:
        return None, "ambiguous: " + "; ".join(p.address for p in exact)
    # Typo tolerance: same house number and a similar street word, if unique.
    similar = [
        p for p, k in keyed
        if any(SequenceMatcher(None, k[1], w).ratio() >= 0.6 for w in words)
    ]
    if len(similar) == 1:
        return similar[0], "fuzzy"
    return None, "no property with that house number and street"
