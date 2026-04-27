"""Breaking-news classification heuristic. No external dependencies."""

from __future__ import annotations

import re

BREAKING_TIER1 = {
    "breaking", "urgent", "alert",
    "crash", "collapse", "bankrupt", "default",
    "emergency", "crisis",
    "war", "attack", "explosion",
    "hack", "breach", "fraud", "scandal",
    "shutdown",
}
BREAKING_TIER2_WORDS = {
    "merger", "acquisition", "ipo",
    "plunge", "surge", "soar",
    "resign", "fired", "arrest", "sanction",
}
BREAKING_TIER2_PHRASES = {"rate cut", "rate hike"}


def is_breaking(title: str, compound: float = 0.0) -> bool:
    words   = set(re.sub(r"[^a-z ]", " ", title.lower()).split())
    t_lower = title.lower()
    if words & BREAKING_TIER1:
        return True
    has_t2 = bool(words & BREAKING_TIER2_WORDS) or any(
        p in t_lower for p in BREAKING_TIER2_PHRASES
    )
    return has_t2 and abs(compound) >= 0.4
