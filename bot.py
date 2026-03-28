#!/usr/bin/env python3
"""
Daily News Digest Bot v2
━━━━━━━━━━━━━━━━━━━━━━━━━
What's new:
  - Sentiment per story  : 📈 Bullish · 📉 Bearish · ⚖️ Neutral  (VADER)
  - Market snapshot      : KLCI, USD/MYR, BTC, S&P 500, Gold  (/market)
  - Breaking news alerts : checks feeds every 30 min, keyword-filtered
  - Better summaries     : first 2 clean sentences, not raw RSS truncation
Commands: /all /world /business /fintech /tech /malaysia /market /help
"""

import asyncio
import html
import logging
import os
import re
from datetime import datetime

import feedparser
import pytz
import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ── Logging ────────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
CHAT_ID     = os.getenv("CHAT_ID",   "")
TIMEZONE    = os.getenv("TIMEZONE",  "Asia/Kuala_Lumpur")
MAX_STORIES = int(os.getenv("MAX_STORIES", "2"))
MAX_MSG_LEN = 4000

# ── Sentiment Engine ──────────────────────────────────────────────────────────────────
_sia = SentimentIntensityAnalyzer()

def sentiment_emoji(text: str) -> str:
    """Return 📈 / 📉 / ⚖️ based on VADER compound score."""
    score = _sia.polarity_scores(text)["compound"]
    if score >= 0.05:
        return "📈"
    elif score <= -0.05:
        return "📉"
    return "⚖️"

# ── RSS Sources ──────────────────────────────────────────────────────────────────────────
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

NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

# ── Breaking News: keywords that warrant an immediate alert ──────────────────────
BREAKING_KEYWORDS = {
    "breaking", "urgent", "alert", "crash", "collapse", "bankrupt",
    "surge", "plunge", "soar", "emergency", "crisis", "resign", "fired",
    "war", "attack", "explosion", "arrest", "ban", "sanction", "recall",
    "ipo", "merger", "acquisition", "rate cut", "rate hike", "default",
    "shutdown", "hack", "breach", "fraud", "scandal",
}

# ── Breaking News State ──────────────────────────────────────────────────────────────────
_seen_links: set = set()
_breaking_initialized: bool = False


# ── Text Helpers ─────────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def clean_summary(raw: str, max_chars: int = 280) -> str:
    """
    Extract the first 1-2 complete sentences from raw RSS text.
    Far cleaner than the old hard-truncation at 180 chars.
    """
    text = strip_html(raw)
    text = re.sub(r"\s+", " ", text).strip()
    # Split on sentence boundaries (period / ! / ? followed by space + capital)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])", text)
    summary = " ".join(sentences[:2]).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "\u2026"
    return summary


def is_breaking(title: str) -> bool:
    """True if the headline contains any high-signal keyword."""
    words = set(re.sub(r"[^a-z ]", " ", title.lower()).split())
    return bool(words & BREAKING_KEYWORDS)


# ── Feed Fetching ──────────────────────────────────────────────────────────────────────────

def fetch_stories(feeds: list, limit: int = MAX_STORIES) -> list:
    """
    Fetch, deduplicate, and return stories enriched with
    sentiment emoji and cleaner summaries.
    """
    all_stories: list = []
    seen: set = set()

    for source, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:12]:
                title = strip_html(entry.get("title", "")).strip()
                if not title:
                    continue
                dedup_key = title.lower()[:60]
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                # Try to get richest possible text for the summary
                raw = ""
                content = entry.get("content")
                if content and isinstance(content, list) and content[0].get("value"):
                    raw = content[0]["value"]
                if not raw or len(strip_html(raw)) < 60:
                    raw = entry.get("summary") or entry.get("description") or ""

                summary   = clean_summary(raw)
                link      = entry.get("link", "")
                sentiment = sentiment_emoji(title + " " + summary)

                all_stories.append({
                    "source":    source,
                    "title":     html.escape(title),
                    "summary":   html.escape(summary),
                    "link":      link,
                    "sentiment": sentiment,
                    "raw_title": title,   # unescaped, for keyword check
                    "raw_link":  link,    # unescaped, for dedup
                })
        except Exception as exc:
            logger.warning("Feed error [%s | %s]: %s", source, url, exc)

    return all_stories[:limit]


# ── Market Snapshot ──────────────────────────────────────────────────────────────────────────

MARKET_TICKERS = [
    ("🇲🇾 KLCI",   "^KLSE",   "{:,.2f}",  " pts"),
    ("💵 USD/MYR", "MYR=X",   "{:.4f}",   ""),
    ("₿  BTC",     "BTC-USD",  "${:,.0f}", ""),
    ("📈 S&P 500", "^GSPC",   "{:,.2f}",  " pts"),
    ("🥇 Gold",    "GC=F",    "${:,.2f}", "/oz"),
]


def get_market_snapshot() -> str:
    tz   = pytz.timezone(TIMEZONE)
    time = datetime.now(tz).strftime("%d %b %Y · %I:%M %p MYT")
    lines = [f"📊 <b>MARKET SNAPSHOT</b>", f"🕐 {time}", ""]

    for label, ticker, fmt, unit in MARKET_TICKERS:
        try:
            fi    = yf.Ticker(ticker).fast_info
            price = fi.last_price
            prev  = fi.previous_close
            if price and prev and prev != 0:
                pct   = ((price - prev) / prev) * 100
                arrow = "🟢" if pct >= 0 else "🔴"
                lines.append(
                    f"{arrow} <b>{label}</b>: {fmt.format(price)}{unit}"
                    f"  <i>({pct:+.2f}%)</i>"
                )
            else:
                lines.append(f"⚪ <b>{label}</b>: N/A")
        except Exception as exc:
            logger.warning("Market [%s]: %s", ticker, exc)
            lines.append(f"⚪ <b>{label}</b>: N/A")

    return "\n".join(lines)


# ── Message Building ───────────────────────────────────────────────────────────────────────

def build_message(category_key: str) -> str:
    cat     = CATEGORIES[category_key]
    stories = fetch_stories(cat["feeds"])
    tz      = pytz.timezone(TIMEZONE)
    date    = datetime.now(tz).strftime("%A, %d %B %Y")

    if not stories:
        return (
            f"{cat['emoji']} <b>{cat['title']}</b>\n\n"
            "⚠️ No stories available right now. Try again shortly."
        )

    lines = [
        f"{cat['emoji']} <b>{cat['title']}</b>",
        f"📅 {date}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for i, s in enumerate(stories):
        num   = NUM_EMOJI[i] if i < len(NUM_EMOJI) else f"{i + 1}."
        block = f"{s['sentiment']} {num} <b>{s['title']}</b>\n<i>{s['source']}</i>"
        if s["summary"]:
            block += f" — {s['summary']}"
        if s["link"]:
            block += f'\n<a href="{s["link"]}">Read more →</a>'
        lines.append(block)
        lines.append("")

    return "\n".join(lines).strip()


# ── Telegram Push ──────────────────────────────────────────────────────────────────────────

async def push(bot, chat_id: str, text: str) -> None:
    """Send a message, splitting recursively if > MAX_MSG_LEN."""
    if len(text) <= MAX_MSG_LEN:
        await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        return
    cut = text[:MAX_MSG_LEN].rfind("\n\n")
    cut = cut if cut > 0 else MAX_MSG_LEN
    await push(bot, chat_id, text[:cut].strip())
    await push(bot, chat_id, text[cut:].strip())


async def deliver(bot, chat_id: str, category_key: str) -> None:
    await push(bot, chat_id, build_message(category_key))


def build_combined_digest(header: str = "", include_market: bool = True) -> str:
    """Build a single digest string with all categories (2 stories each)."""
    tz   = pytz.timezone(TIMEZONE)
    date = datetime.now(tz).strftime("%A, %d %B %Y")

    parts = [header or f"🌅 <b>Daily News Digest</b>\n📅 {date}"]

    if include_market:
        parts.append(get_market_snapshot())

    for key in CATEGORIES:
        parts.append(build_message(key))

    return "\n\n━━━━━━━━━━━━━━━━━━━━━\n\n".join(parts)


# ── Command Handlers ───────────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "👋 <b>Daily News Digest Bot v2</b>\n\n"
    "Auto-digest at <b>8:00 AM MYT</b> · Breaking alerts every 30 min\n"
    "Pull any category on demand, anytime.\n\n"
    "📌 <b>Commands:</b>\n"
    "/all      — 📰 Full digest + market\n"
    "/world    — 🌍 World News\n"
    "/business — 💼 Business News\n"
    "/fintech  — 💳 Fintech &amp; Crypto\n"
    "/tech     — 💻 Technology\n"
    "/malaysia — 🇲🇾 Malaysian News\n"
    "/market   — 📊 Live Market Snapshot\n"
    "/help     — ❓ This menu\n\n"
    "<i>Stories are tagged: 📈 Bullish · 📉 Bearish · ⚖️ Neutral</i>"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_TEXT)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_TEXT)


async def _run_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    cat    = CATEGORIES[key]
    loader = await update.message.reply_text(f"⏳ Fetching {cat['emoji']} {cat['title']}…")
    await deliver(ctx.bot, str(update.effective_chat.id), key)
    try:
        await loader.delete()
    except Exception:
        pass


async def cmd_world(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_category(update, ctx, "world")


async def cmd_business(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_category(update, ctx, "business")


async def cmd_fintech(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_category(update, ctx, "fintech")


async def cmd_tech(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_category(update, ctx, "tech")


async def cmd_malaysia(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_category(update, ctx, "malaysia")


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    loader = await update.message.reply_text("⏳ Fetching live market data…")
    await push(ctx.bot, str(update.effective_chat.id), get_market_snapshot())
    try:
        await loader.delete()
    except Exception:
        pass


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    loader  = await update.message.reply_text("⏳ Compiling full digest…")
    await push(ctx.bot, chat_id, build_combined_digest(include_market=True))
    try:
        await loader.delete()
    except Exception:
        pass


# ── Scheduled: Daily Digest at 8 AM ─────────────────────────────────────────────────────

async def daily_digest(app: Application) -> None:
    logger.info("Running daily digest…")
    try:
        tz   = pytz.timezone(TIMEZONE)
        date = datetime.now(tz).strftime("%A, %d %B %Y")
        header = f"🌅 <b>Good Morning, Rodney! Your Daily Digest</b>\n📅 {date}"
        await push(app.bot, CHAT_ID, build_combined_digest(header=header, include_market=True))
    except Exception as exc:
        logger.error("Daily digest failed: %s", exc)


# ── Scheduled: Breaking News every 30 min ───────────────────────────────────────────────────

async def check_breaking_news(app: Application) -> None:
    """
    Runs every 30 minutes. On the first run, it only populates _seen_links
    (no alerts sent). Afterwards, any new story whose headline contains a
    high-signal keyword triggers an immediate Telegram alert.
    """
    global _seen_links, _breaking_initialized

    breaking_stories: dict = {}   # category_key -> [story, ...]

    for key, cat in CATEGORIES.items():
        stories = fetch_stories(cat["feeds"], limit=10)
        new = [s for s in stories if s["raw_link"] and s["raw_link"] not in _seen_links]
        for s in stories:
            if s["raw_link"]:
                _seen_links.add(s["raw_link"])
        # Filter to only genuinely newsworthy stories
        hot = [s for s in new if is_breaking(s["raw_title"])]
        if hot:
            breaking_stories[key] = hot

    if not _breaking_initialized:
        _breaking_initialized = True
        logger.info("Breaking-news tracker ready. %d links indexed.", len(_seen_links))
        return

    if not breaking_stories:
        return

    total = sum(len(v) for v in breaking_stories.values())
    logger.info("Breaking news: %d story/stories to push.", total)

    await push(
        app.bot, CHAT_ID,
        f"🚨 <b>Breaking News</b> — {total} new alert{'s' if total > 1 else ''}",
    )
    for key, stories in breaking_stories.items():
        cat   = CATEGORIES[key]
        lines = [f"{cat['emoji']} <b>{cat['title']}</b>", ""]
        for s in stories[:3]:
            block = f"{s['sentiment']} <b>{s['title']}</b>\n<i>{s['source']}</i>"
            if s["summary"]:
                block += f" — {s['summary']}"
            if s["link"]:
                block += f'\n<a href="{s["link"]}">Read more →</a>'
            lines.extend([block, ""])
        await push(app.bot, CHAT_ID, "\n".join(lines).strip())
        await asyncio.sleep(0.3)


# ── Boot ────────────────────────────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("all",      "📰 Full digest + market"),
        BotCommand("world",    "🌍 World News"),
        BotCommand("business", "💼 Business News"),
        BotCommand("fintech",  "💳 Fintech & Crypto"),
        BotCommand("tech",     "💻 Technology"),
        BotCommand("malaysia", "🇲🇾 Malaysian News"),
        BotCommand("market",   "📊 Live Market Snapshot"),
        BotCommand("help",     "❓ Help"),
    ])
    logger.info("Commands menu registered.")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    for cmd, fn in [
        ("start",    cmd_start),
        ("help",     cmd_help),
        ("world",    cmd_world),
        ("business", cmd_business),
        ("fintech",  cmd_fintech),
        ("tech",     cmd_tech),
        ("malaysia", cmd_malaysia),
        ("market",   cmd_market),
        ("all",      cmd_all),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(daily_digest,         "cron",     hour=8, minute=0,  args=[app])
    scheduler.add_job(check_breaking_news,  "interval", minutes=30,        args=[app])
    scheduler.start()

    logger.info("Bot v2 live — 8AM digest · 30-min breaking alerts · sentiment · market.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
