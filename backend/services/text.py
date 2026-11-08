from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9.]+")


def normalize_text(value: str | None) -> str:
    """Normalise a printed item description for exact alias lookup.

    Lower-cases, keeps letters, digits and dots (sizes like "1.5mm"), and collapses everything
    else to single spaces: ``"63MM  uPVC-Pipe"`` -> ``"63mm upvc pipe"``.
    """
    if not value:
        return ""
    return _NON_ALNUM.sub(" ", value.lower()).strip()
