#!/usr/bin/env python3
"""Breaking-news entry point — runs every 30 min, sends max 1 alert/day.

Triggered by .github/workflows/breaking.yml on cron */30.
State (last_alert_date, seen_links) persisted via actions/cache between runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytz
from telegram import Bot

from breaking import is_breaking
from feeds import CATEGORIES, fetch_coingecko_stories, fetch_stories
from formatting import format_story_block

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["CHAT_ID"]
TIMEZONE  = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")

STATE_DIR  = Path("state")
STATE_FILE = STATE_DIR / "breaking.json"

MAX_DAILY_ALERTS = 1
SEEN_TTL_HOURS   = 24


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            cutoff = datetime.now().timestamp() - SEEN_TTL_HOURS * 3600
            data["seen_links"] = {
                link: ts for link, ts in data.get("seen_links", {}).items()
                if datetime.fromisoformat(ts).timestamp() > cutoff
            }
            return data
    except Exception as exc:
        logger.warning("load_state failed: %s", exc)
    return {"last_alert_date": "", "seen_links": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


async def _fetch_category(key: str, cat: dict) -> list:
    stories = await asyncio.to_thread(
        fetch_stories,
        cat["feeds"], 10,
        cat.get("geo_keywords"),
        cat.get("english_only", False),
    )
    if cat.get("include_coingecko"):
        crypto = await asyncio.to_thread(fetch_coingecko_stories, 5)
        stories.extend(crypto)
    return stories


async def main() -> None:
    tz       = pytz.timezone(TIMEZONE)
    today    = datetime.now(tz).strftime("%Y-%m-%d")
    now_iso  = datetime.now(tz).isoformat()

    state = load_state()

    # 1/day cap
    if state.get("last_alert_date") == today:
        logger.info("Today's alert already sent — exiting.")
        save_state(state)  # ensures cache is refreshed even on no-op runs
        return

    # Fetch all categories
    tasks   = {k: _fetch_category(k, c) for k, c in CATEGORIES.items()}
    fetched = await asyncio.gather(*tasks.values())
    by_key  = dict(zip(tasks.keys(), fetched))

    seen     = state.get("seen_links", {})
    breaking = []  # list of (key, cat, story)

    for key, cat in CATEGORIES.items():
        for s in by_key[key]:
            link = s.get("raw_link")
            if not link:
                continue
            if link in seen:
                continue
            seen[link] = now_iso
            if is_breaking(s["raw_title"], s.get("compound", 0.0)):
                breaking.append((key, cat, s))

    state["seen_links"] = seen

    if not breaking:
        save_state(state)
        logger.info("No breaking stories. Indexed %d links.", len(seen))
        return

    # Take just the first one (cap at 1/day)
    take = breaking[:MAX_DAILY_ALERTS]
    lines = ["🚨 <b>Breaking News</b>", "━━━━━━━━━━━━━━━━━━━━━", ""]
    for idx, (k, cat, s) in enumerate(take):
        lines.append(format_story_block(idx, k, cat, s))
        lines.append("")
    text = "\n".join(lines).strip()

    bot = Bot(token=BOT_TOKEN)
    async with bot:
        await bot.send_message(
            chat_id=CHAT_ID, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )

    state["last_alert_date"] = today
    save_state(state)
    logger.info("Sent %d breaking alert(s).", len(take))


if __name__ == "__main__":
    asyncio.run(main())
