"""Readable identifiers for shows, models and scenes."""

import re
import secrets
from collections.abc import Collection


def slugify(text: str) -> str:
    """Lower-case word characters joined by dashes; keeps non-Latin letters (e.g. Kazakh)."""
    return re.sub(r"[\W_]+", "-", text.lower()).strip("-")


def new_show_id(title: str) -> str:
    slug = slugify(title)[:40] or "show"
    return f"{slug}-{secrets.token_hex(2)}"


def unique_id(prefix: str, name: str, taken: Collection[str]) -> str:
    """``<prefix>_<slug>``, suffixed with a number when already taken."""
    base = f"{prefix}_{slugify(name).replace('-', '_') or prefix}"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate
