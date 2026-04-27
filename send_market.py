#!/usr/bin/env python3
"""Market snapshot entry point — sends snapshot text + 7-day chart, exits.

Triggered by .github/workflows/market.yml at 10:00 MYT (02:00 UTC).
"""

from __future__ import annotations

import asyncio
import logging
import os

from telegram import Bot

from market import generate_market_chart, get_market_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["CHAT_ID"]


async def main() -> None:
    snapshot = await asyncio.to_thread(get_market_snapshot)
    png      = await asyncio.to_thread(generate_market_chart)

    bot = Bot(token=BOT_TOKEN)
    async with bot:
        if png is not None:
            await bot.send_photo(
                chat_id=CHAT_ID, photo=png,
                caption=snapshot, parse_mode="HTML",
            )
            logger.info("Sent market snapshot with chart (%d bytes).", len(png))
        else:
            await bot.send_message(
                chat_id=CHAT_ID, text=snapshot, parse_mode="HTML",
            )
            logger.info("Sent market snapshot (no chart).")


if __name__ == "__main__":
    asyncio.run(main())
