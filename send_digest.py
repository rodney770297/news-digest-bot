#!/usr/bin/env python3
"""Daily digest entry point — fetches all categories, sends one consolidated message, exits.

Triggered by .github/workflows/digest.yml at 08:00 MYT (00:00 UTC).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import pytz
from telegram import Bot

from feeds import CATEGORIES, fetch_coingecko_stories, fetch_stories
from formatting import format_story_block

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["CHAT_ID"]
TIMEZONE  = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")

STORIES_PER_CATEGORY = 2
MAX_MSG_LEN          = 4000


async def _fetch_category(key: str, cat: dict) -> list:
    stories = await asyncio.to_thread(
        fetch_stories,
        cat["feeds"],
        STORIES_PER_CATEGORY * 3,           # over-fetch then trim post-merge
        cat.get("geo_keywords"),
        cat.get("english_only", False),
    )
    if cat.get("include_coingecko"):
        crypto = await asyncio.to_thread(fetch_coingecko_stories, 2)
        stories.extend(crypto)
    return stories[:STORIES_PER_CATEGORY]


async def _send_chunked(bot: Bot, text: str) -> None:
    if len(text) <= MAX_MSG_LEN:
        await bot.send_message(
            chat_id=CHAT_ID, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        return
    cut = text[:MAX_MSG_LEN].rfind("\n\n")
    cut = cut if cut > 0 else MAX_MSG_LEN
    await _send_chunked(bot, text[:cut].strip())
    await _send_chunked(bot, text[cut:].strip())


async def main() -> None:
    tz   = pytz.timezone(TIMEZONE)
    date = datetime.now(tz).strftime("%A, %d %B %Y")

    tasks   = {k: _fetch_category(k, c) for k, c in CATEGORIES.items()}
    fetched = await asyncio.gather(*tasks.values())
    by_key  = dict(zip(tasks.keys(), fetched))

    lines: list = [
        f"🌅 <b>Daily Digest — {date}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    idx = 0
    for key, cat in CATEGORIES.items():
        for s in by_key[key]:
            lines.append(format_story_block(idx, key, cat, s))
            lines.append("")
            idx += 1

    if idx == 0:
        text = "🌅 <b>Daily Digest</b>\n\n⚠️ No stories available right now."
    else:
        text = "\n".join(lines).strip()

    bot = Bot(token=BOT_TOKEN)
    async with bot:
        await _send_chunked(bot, text)
    logger.info("Sent digest with %d stories.", idx)


if __name__ == "__main__":
    asyncio.run(main())
