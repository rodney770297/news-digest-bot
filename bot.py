#!/usr/bin/env python3
"""
Daily News Digest - Telegram Bot
Sources  : RSS feeds (no API key needed)
Schedule : Daily at 8:00 AM MYT (Asia/Kuala_Lumpur)
Commands : /all /world /business /fintech /tech /malaysia /help
Deploy   : Railway, Render, any Linux VPS
"""

import asyncio
import html
import logging
import os
import re
from datetime import datetime

import feedparser
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN   = os.getenv("BOT_TOKEN", "8692467740:AAEc4VOqkGbVa472OAeTTUKicbwzvnLFTSg")
CHAT_ID     = os.getenv("CHAT_ID",   "3591286")
TIMEZONE    = os.getenv("TIMEZONE",  "Asia/Kuala_Lumpur")
MAX_STORIES = int(os.getenv("MAX_STORIES", "5"))
MAX_MSG_LEN = 4000

CATEGORIES = {
    "world": {
        "emoji": "🌍", "title": "WORLD NEWS",
        "feeds": [
            ("BBC World",   "https://feeds.bbci.co.uk/news/world/rss.xml"),
            ("Reuters",     "https://feeds.reuters.com/reuters/worldNews"),
            ("AP News",     "https://apnews.com/apf-topnews/rss"),
        ],
    },
    "business": {
        "emoji": "💼", "title": "BUSINESS NEWS",
        "feeds": [
            ("Reuters Biz",  "https://feeds.reuters.com/reuters/businessNews"),
            ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
            ("CNBC",         "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
        ],
    },
    "fintech": {
        "emoji": "💳", "title": "FINTECH",
        "feeds": [
            ("Finextra",  "https://www.finextra.com/rss/finextra-news.xml"),
            ("PYMNTS",    "https://www.pymnts.com/feed/"),
            ("CoinDesk",  "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ],
    },
    "tech": {
        "emoji": "💻", "title": "TECHNOLOGY",
        "feeds": [
            ("TechCrunch",   "https://techcrunch.com/feed/"),
            ("The Verge",    "https://www.theverge.com/rss/index.xml"),
            ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ],
    },
    "malaysia": {
        "emoji": "🇲🇾", "title": "MALAYSIAN NEWS",
        "feeds": [
            ("Malay Mail",  "https://www.malaymail.com/feed"),
            ("FMT News",    "https://www.freemalaysiatoday.com/feed/"),
            ("The Star",    "https://www.thestar.com.my/rss/Business/"),
        ],
    },
}

NUM_EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣"]

def strip_html(text):
    return re.sub(r"<[^>]+>", " ", text).strip()

def fetch_stories(feeds, limit=MAX_STORIES):
    all_stories, seen = [], set()
    for source, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = strip_html(entry.get("title", "")).strip()
                if not title: continue
                key = title.lower()[:60]
                if key in seen: continue
                seen.add(key)
                raw = entry.get("summary") or entry.get("description") or ""
                summary = strip_html(raw)
                if len(summary) > 180:
                    summary = summary[:180].rsplit(" ", 1)[0] + "..."
                all_stories.append({"source": source, "title": html.escape(title), "summary": html.escape(summary), "link": entry.get("link", "")})
        except Exception as exc:
            logger.warning("Feed error [%s]: %s", source, exc)
    return all_stories[:limit]

def build_message(category_key):
    cat = CATEGORIES[category_key]
    stories = fetch_stories(cat["feeds"])
    tz = pytz.timezone(TIMEZONE)
    date = datetime.now(tz).strftime("%A, %d %B %Y")
    if not stories:
        return f"{cat['emoji']} <b>{cat['title']}</b>\n\nNo stories available right now."
    lines = [f"{cat['emoji']} <b>{cat['title']}</b>", f"\U0001F4C5 {date}", "---", ""]
    for i, s in enumerate(stories):
        num = NUM_EMOJI[i] if i < len(NUM_EMOJI) else f"{i+1}."
        block = f"{num} <b>{s['title']}</b>\n<i>{s['source']}</i>"
        if s["summary"]: block += f" - {s['summary']}"
        if s["link"]: block += f'\n<a href="{s["link"]}">Read more</a>'
        lines.extend([block, ""])
    return "\n".join(lines).strip()

async def push(bot, chat_id, text):
    if len(text) <= MAX_MSG_LEN:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
        return
    cut = text[:MAX_MSG_LEN].rfind("\n\n")
    cut = cut if cut > 0 else MAX_MSG_LEN
    await push(bot, chat_id, text[:cut].strip())
    await push(bot, chat_id, text[cut:].strip())

async def deliver(bot, chat_id, category_key):
    await push(bot, chat_id, build_message(category_key))

HELP_TEXT = ("\U0001F44B <b>Daily News Digest Bot</b>\n\nFresh news at <b>8:00 AM MYT</b> daily. Pull any category anytime.\n\n"
    "\U0001F4CC <b>Commands:</b>\n/all - Full digest\n/world - World\n/business - Business\n/fintech - Fintech\n/tech - Tech\n/malaysia - Malaysia\n/help - Help")

async def cmd_start(update, ctx): await update.message.reply_html(HELP_TEXT)
async def cmd_help(update, ctx): await update.message.reply_html(HELP_TEXT)

async def _run_category(update, ctx, key):
    cat = CATEGORIES[key]
    loader = await update.message.reply_text(f"Fetching {cat['title']}...")
    await deliver(ctx.bot, str(update.effective_chat.id), key)
    try: await loader.delete()
    except: pass

async def cmd_world(update, ctx): await _run_category(update, ctx, "world")
async def cmd_business(update, ctx): await _run_category(update, ctx, "business")
async def cmd_fintech(update, ctx): await _run_category(update, ctx, "fintech")
async def cmd_tech(update, ctx): await _run_category(update, ctx, "tech")
async def cmd_malaysia(update, ctx): await _run_category(update, ctx, "malaysia")

async def cmd_all(update, ctx):
    loader = await update.message.reply_text("Compiling full digest...")
    tz = pytz.timezone(TIMEZONE)
    date = datetime.now(tz).strftime("%A, %d %B %Y")
    await push(ctx.bot, str(update.effective_chat.id), f"<b>Daily News Digest</b>\n{date}")
    for key in CATEGORIES:
        await deliver(ctx.bot, str(update.effective_chat.id), key)
        await asyncio.sleep(0.4)
    try: await loader.delete()
    except: pass

async def daily_digest(app):
    tz = pytz.timezone(TIMEZONE)
    date = datetime.now(tz).strftime("%A, %d %B %Y")
    await push(app.bot, CHAT_ID, f"Good Morning, Rodney! Your Daily Digest\n{date}")
    for key in CATEGORIES:
        try:
            await deliver(app.bot, CHAT_ID, key)
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Failed: %s %s", key, exc)

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("all","Full digest"), BotCommand("world","World News"),
        BotCommand("business","Business News"), BotCommand("fintech","Fintech"),
        BotCommand("tech","Technology"), BotCommand("malaysia","Malaysian News"),
        BotCommand("help","Help"),
    ])

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    for cmd, fn in [("start",cmd_start),("help",cmd_help),("world",cmd_world),
                    ("business",cmd_business),("fintech",cmd_fintech),
                    ("tech",cmd_tech),("malaysia",cmd_malaysia),("all",cmd_all)]:
        app.add_handler(CommandHandler(cmd, fn))
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(daily_digest, trigger="cron", hour=8, minute=0, args=[app])
    scheduler.start()
    logger.info("Bot running.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
