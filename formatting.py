"""Text helpers and story formatting."""

from __future__ import annotations

import html
import re

NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def clean_summary(raw: str, max_chars: int = 280) -> str:
    text      = strip_html(raw)
    text      = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'“])", text)
    summary   = " ".join(sentences[:2]).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "…"
    return summary


def one_liner(raw: str, max_chars: int = 140) -> str:
    text      = strip_html(raw)
    text      = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'“])", text)
    line      = sentences[0].strip() if sentences else text
    if len(line) > max_chars:
        line = line[:max_chars].rsplit(" ", 1)[0] + "…"
    return line


def format_story_block(idx: int, key: str, cat: dict, s: dict) -> str:
    """One compact block per story with category tag + one-liner."""
    num   = NUM_EMOJI[idx] if idx < len(NUM_EMOJI) else f"{idx + 1}."
    tag   = f"{cat['emoji']} <b>[{key.upper()}]</b>"
    desc  = s.get("oneline") or s.get("summary") or ""
    block = f"{num} {tag} <b>{s['title']}</b>"
    if desc:
        block += f"\n<i>{desc}</i>"
    meta = s["source"]
    if s.get("link"):
        safe_link = html.escape(s["link"], quote=True)
        meta += f' · <a href="{safe_link}">Read →</a>'
    block += f"\n<i>{meta}</i>"
    return block
