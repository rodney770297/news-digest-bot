#!/usr/bin/env python3
"""
Daily News Digest Bot v2.1
━━━━━━━━━━━━━━━━━━━━━━━━━
What's new in v2.1:
  - 👍/👎 inline rating buttons on every digest & breaking alert
  - Preference engine: keyword + source + category scoring
  - Dislike weight = 3× like weight (you only like the extraordinary)
  - Persistent prefs: /data/preferences.json on Railway Volume
  - /myprofile: see what the bot has learned about you
  - Personalisation kicks in after 5 ratings
Commands: /all /world /business /fintech /tech /malaysia /market /myprofile /help
"""

import asyncio
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import feedparser
import pytz
import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
CHAT_ID     = os.getenv("CHAT_ID",   "")
TIMEZONE    = os.getenv("TIMEZONE",  "Asia/Kuala_Lumpur")
MAX_STORIES = int(os.getenv("MAX_STORIES", "5"))
MAX_MSG_LEN = 4000
DATA_DIR    = os.getenv("DATA_DIR", "/data")
PREFS_FILE  = Path(DATA_DIR) / "preferences.json"

# ── Sentiment Engine ───────────────────────────────────────────────────────────
_sia = SentimentIntensityAnalyzer()

# ── RSS Sources ────────────────────────────────────────────────────────────────
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
        "geo_keywords": {
            "malaysia", "malaysian", "kuala lumpur", "klci", "ringgit",
            "bursa", "putrajaya", "sabah", "sarawak", "penang", "johor",
            "perak", "selangor", "petaling jaya", "myr", "kl ",
            "mahathir", "anwar ibrahim", "najib", "umno", "pakatan",
            "petronas", "khazanah", "tabung haji", "epf", "kwsp",
            "bank negara", "sc malaysia", "idrisi", "iskandar",
            "1malaysia", "mida", "mdec", "mycert",
        },
        "english_only": True,
    },
}

NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

# ── Breaking News ──────────────────────────────────────────────────────────────
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
MAX_DAILY_ALERTS = 5

# ── Breaking News State ────────────────────────────────────────────────────────
_seen_links: set           = set()
_breaking_initialized: bool = False
_daily_alert_count: int    = 0
_daily_alert_date: str     = ""

# ── Personalisation: weights ───────────────────────────────────────────────────
LIKE_WEIGHT    = 1.0
DISLIKE_WEIGHT = 3.0
MIN_RATINGS    = 5
STORY_CACHE_MAX = 200

# Common Malay function words that never appear in standard English.
MALAY_INDICATORS = {
    "yang", "kepada", "bahawa", "kerana", "daripada", "tidak", "akan",
    "telah", "dengan", "untuk", "bagi", "apabila", "seperti", "antara",
    "lebih", "boleh", "dalam", "rakyat", "kerajaan", "perdana", "menteri",
    "perikatan", "nasional", "parti", "ahli", "parlimen", "dewan",
    "berkata", "semua", "mereka", "adalah", "jika", "hanya",
    "satu", "dua", "tiga", "empat", "lima",
}

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "its", "it",
    "this", "that", "these", "those", "i", "we", "you", "he", "she",
    "they", "as", "up", "out", "about", "into", "over", "after",
    "says", "said", "report", "reports", "according", "amid", "just",
    "than", "more", "also", "since", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "amid", "amid",
}


# ── Preference I/O ─────────────────────────────────────────────────────────────

def _default_prefs() -> dict:
    return {
        "keywords":     {},
        "sources":      {},
        "categories":   {},
        "total_ratings": 0,
        "story_cache":  {},
        "last_updated": "",
    }


def load_prefs() -> dict:
    try:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
        if PREFS_FILE.exists():
            return json.loads(PREFS_FILE.read_text())
    except Exception as exc:
        logger.warning("Could not load prefs: %s", exc)
    return _default_prefs()


def save_prefs(prefs: dict) -> None:
    try:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(prefs, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.warning("Could not save prefs: %s", exc)


# ── Preference Logic ───────────────────────────────────────────────────────────

def extract_keywords(title: str) -> list:
    words = re.sub(r"[^a-z ]", " ", title.lower()).split()
    return [w for w in words if w not in STOP_WORDS and len(w) >= 4]


def story_hash(title: str) -> str:
    return hashlib.md5(title.encode()).hexdigest()[:8]


def score_story(raw_title: str, source: str, category: str, prefs: dict) -> float:
    if prefs.get("total_ratings", 0) < MIN_RATINGS:
        return 0.0
    score = 0.0
    for kw in extract_keywords(raw_title):
        score += prefs["keywords"].get(kw, 0.0)
    score += prefs["sources"].get(source.lower(), 0.0)
    score += prefs["categories"].get(category, 0.0)
    return score


def rank_stories(stories: list, category: str, prefs: dict) -> list:
    if prefs.get("total_ratings", 0) < MIN_RATINGS:
        return stories
    scored = [
        (score_story(s["raw_title"], s["source"], category, prefs), i, s)
        for i, s in enumerate(stories)
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored]


def cache_stories(stories: list, category: str, prefs: dict) -> None:
    cache = prefs.setdefault("story_cache", {})
    for s in stories:
        h = story_hash(s["raw_title"])
        cache[h] = {
            "title":    s["raw_title"],
            "source":   s["source"],
            "category": category,
        }
    if len(cache) > STORY_CACHE_MAX:
        for old_key in list(cache.keys())[:-STORY_CACHE_MAX]:
            del cache[old_key]


def record_rating(title: str, source: str, category: str, vote: str, prefs: dict) -> None:
    sign = LIKE_WEIGHT if vote == "like" else -DISLIKE_WEIGHT
    for kw in extract_keywords(title):
        prefs["keywords"][kw] = round(prefs["keywords"].get(kw, 0.0) + sign, 2)
    src = source.lower()
    prefs["sources"][src] = round(prefs["sources"].get(src, 0.0) + sign * 0.5, 2)
    prefs["categories"][category] = round(prefs["categories"].get(category, 0.0) + sign * 0.3, 2)
    prefs["total_ratings"] = prefs.get("total_ratings", 0) + 1
    prefs["last_updated"] = datetime.now(pytz.timezone(TIMEZONE)).isoformat()


# ── Inline Rating Keyboard ─────────────────────────────────────────────────────

def build_rating_keyboard(stories: list) -> InlineKeyboardMarkup:
    rows: list = []
    row:  list = []
    for i, s in enumerate(stories):
        h   = story_hash(s["raw_title"])
        num = NUM_EMOJI[i] if i < len(NUM_EMOJI) else f"{i + 1}"
        row.append(InlineKeyboardButton(f"{num}👍", callback_data=f"r:like:{h}"))
        row.append(InlineKeyboardButton(f"{num}👎", callback_data=f"r:dislike:{h}"))
        if len(row) >= 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


# ── Text Helpers ───────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def clean_summary(raw: str, max_chars: int = 280) -> str:
    text      = strip_html(raw)
    text      = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])", text)
    summary   = " ".join(sentences[:2]).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "…"
    return summary


def is_breaking(title: str, compound: float = 0.0) -> bool:
    words   = set(re.sub(r"[^a-z ]", " ", title.lower()).split())
    t_lower = title.lower()
    if words & BREAKING_TIER1:
        return True
    has_t2 = bool(words & BREAKING_TIER2_WORDS) or any(p in t_lower for p in BREAKING_TIER2_PHRASES)
    return has_t2 and abs(compound) >= 0.4


# ── Feed Fetching ──────────────────────────────────────────────────────────────

_punct_re = re.compile(r"[^\w\s]")


def _make_dedup_key(title: str) -> str:
    clean = _punct_re.sub("", title.lower())
    words = [w for w in clean.split() if len(w) > 2][:8]
    return " ".join(words)


def fetch_stories(
    feeds: list,
    limit: int = MAX_STORIES,
    geo_keywords: set | None = None,
    english_only: bool = False,
) -> list:
    all_stories: list  = []
    seen_titles: set   = set()
    seen_links:  set   = set()

    for source, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:12]:
                title = strip_html(entry.get("title", "")).strip()
                if not title:
                    continue

                # ── Language filter ────────────────────────────────────────
                if english_only:
                    title_words = set(re.sub(r"[^a-z ]", " ", title.lower()).split())
                    if title_words & MALAY_INDICATORS:
                        continue

                # ── Deduplication ──────────────────────────────────────────
                dedup_key = _make_dedup_key(title)
                if dedup_key in seen_titles:
                    continue

                link     = entry.get("link", "")
                link_key = link.split("?")[0].rstrip("/") if link else ""
                if link_key and link_key in seen_links:
                    continue

                seen_titles.add(dedup_key)
                if link_key:
                    seen_links.add(link_key)

                raw = ""
                content = entry.get("content")
                if content and isinstance(content, list) and content[0].get("value"):
                    raw = content[0]["value"]
                if not raw or len(strip_html(raw)) < 60:
                    raw = entry.get("summary") or entry.get("description") or ""

                summary = clean_summary(raw)

                # ── Geographic filter ──────────────────────────────────────
                if geo_keywords:
                    haystack = (title + " " + strip_html(raw)).lower()
                    if not any(kw in haystack for kw in geo_keywords):
                        continue

                compound = _sia.polarity_scores(title + " " + summary)["compound"]
                sentiment = (
                    "📈" if compound >= 0.05 else
                    "📉" if compound <= -0.05 else
                    "⚖️"
                )

                all_stories.append({
                    "source":    source,
                    "title":     html.escape(title),
                    "summary":   html.escape(summary),
                    "link":      link,
                    "sentiment": sentiment,
                    "compound":  compound,
                    "raw_title": title,
                    "raw_link":  link,
                })
        except Exception as exc:
            logger.warning("Feed error [%s | %s]: %s", source, url, exc)

    return all_stories[:limit]


# ── Market Snapshot ────────────────────────────────────────────────────────────

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
            hist = yf.Ticker(ticker).history(period="1mo")
            if hist.empty or len(hist) < 2:
                lines.append(f"⚪ <b>{label}</b>: N/A")
                continue
            price = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2])
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


# ── Message Building ───────────────────────────────────────────────────────────

def build_message(category_key: str, prefs: dict) -> tuple:
    cat          = CATEGORIES[category_key]
    geo_keywords = cat.get("geo_keywords")
    english_only = cat.get("english_only", False)
    stories      = fetch_stories(cat["feeds"], geo_keywords=geo_keywords, english_only=english_only)
    stories      = rank_stories(stories, category_key, prefs)
    cache_stories(stories, category_key, prefs)

    tz   = pytz.timezone(TIMEZONE)
    date = datetime.now(tz).strftime("%A, %d %B %Y")

    if not stories:
        text = (
            f"{cat['emoji']} <b>{cat['title']}</b>\n\n"
            "⚠️ No stories available right now. Try again shortly."
        )
        return text, None

    total = prefs.get("total_ratings", 0)
    if total < MIN_RATINGS:
        remaining = MIN_RATINGS - total
        pref_hint = f"\n<i>🎯 Rate {remaining} more stor{'y' if remaining == 1 else 'ies'} to unlock personalisation</i>"
    else:
        pref_hint = "\n<i>🎯 Personalised for you</i>"

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

    lines.append(pref_hint)
    lines.append("<i>Rate stories below 👇</i>")

    text     = "\n".join(lines).strip()
    keyboard = build_rating_keyboard(stories)
    return text, keyboard


# ── Telegram Push ──────────────────────────────────────────────────────────────

async def push(
    bot, chat_id: str, text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if len(text) <= MAX_MSG_LEN:
        await bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return
    cut = text[:MAX_MSG_LEN].rfind("\n\n")
    cut = cut if cut > 0 else MAX_MSG_LEN
    await push(bot, chat_id, text[:cut].strip())
    await push(bot, chat_id, text[cut:].strip(), reply_markup)


async def deliver(bot, chat_id: str, category_key: str, prefs: dict) -> None:
    text, keyboard = build_message(category_key, prefs)
    await push(bot, chat_id, text, keyboard)


# ── Rating Callback Handler ────────────────────────────────────────────────────

async def handle_rating(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "r":
        return

    _, vote, h = parts
    if vote not in ("like", "dislike"):
        return

    prefs  = load_prefs()
    cached = prefs.get("story_cache", {}).get(h)
    if not cached:
        await query.answer("⏱ Story has expired from cache — can't rate it now.", show_alert=True)
        return

    record_rating(cached["title"], cached["source"], cached["category"], vote, prefs)
    save_prefs(prefs)

    emoji  = "👍" if vote == "like" else "👎"
    total  = prefs["total_ratings"]
    if total < MIN_RATINGS:
        msg = f"{emoji} Noted! {MIN_RATINGS - total} more rating(s) to unlock personalisation."
    else:
        msg = f"{emoji} Got it — feed updated."

    await query.answer(msg, show_alert=False)
    logger.info("Rating [%s] %s — total: %d", vote, cached["title"][:50], total)


# ── Command Handlers ───────────────────────────────────────────────────────────

HELP_TEXT = (
    "👋 <b>Daily News Digest Bot v2.1</b>\n\n"
    "Auto-digest at <b>8:00 AM MYT</b> · Breaking alerts every 30 min\n"
    "Rate stories with 👍/👎 buttons to personalise your feed.\n\n"
    "📌 <b>Commands:</b>\n"
    "/all       — 📰 Full digest + market\n"
    "/world     — 🌍 World News\n"
    "/business  — 💼 Business News\n"
    "/fintech   — 💳 Fintech &amp; Crypto\n"
    "/tech      — 💻 Technology\n"
    "/malaysia  — 🇲🇾 Malaysian News\n"
    "/market    — 📊 Live Market Snapshot\n"
    "/myprofile — 🧠 Your preference profile\n"
    "/help      — ❓ This menu\n\n"
    "<i>Stories tagged: 📈 Bullish · 📉 Bearish · ⚖️ Neutral\n"
    "Personalisation: 👎 = 3× weight of 👍</i>"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_TEXT)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP_TEXT)


async def _run_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    cat    = CATEGORIES[key]
    loader = await update.message.reply_text(f"⏳ Fetching {cat['emoji']} {cat['title']}…")
    prefs  = load_prefs()
    await deliver(ctx.bot, str(update.effective_chat.id), key, prefs)
    save_prefs(prefs)
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


def build_all_message(prefs: dict) -> tuple:
    tz    = pytz.timezone(TIMEZONE)
    date  = datetime.now(tz).strftime("%A, %d %B %Y")
    total = prefs.get("total_ratings", 0)
    n     = 2 if total >= MIN_RATINGS else 1

    if total < MIN_RATINGS:
        remaining = MIN_RATINGS - total
        pref_hint = (
            f"\n<i>🎯 Rate {remaining} more stor"
            f"{'y' if remaining == 1 else 'ies'} to unlock personalisation</i>"
        )
    else:
        pref_hint = "\n<i>🎯 Personalised for you</i>"

    lines: list       = [f"🌅 <b>Top Headlines — {date}</b>", ""]
    all_stories: list = []

    for key, cat in CATEGORIES.items():
        stories = fetch_stories(cat["feeds"])
        stories = rank_stories(stories, key, prefs)
        cache_stories(stories, key, prefs)
        top = stories[:n]
        if not top:
            continue

        lines.append(f"{cat['emoji']} <b>{cat['title']}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        for s in top:
            idx = len(all_stories)
            num = NUM_EMOJI[idx] if idx < len(NUM_EMOJI) else f"{idx + 1}."
            block = f"{s['sentiment']} {num} <b>{s['title']}</b>\n<i>{s['source']}</i>"
            if s["summary"]:
                block += f" — {s['summary']}"
            if s["link"]:
                block += f'\n<a href="{s["link"]}">Read more →</a>'
            lines.append(block)
            all_stories.append(s)

        lines.append("")

    if not all_stories:
        return "⚠️ No stories available right now. Try again shortly.", None

    lines.append(pref_hint)
    lines.append("<i>Rate stories below 👇</i>")

    text     = "\n".join(lines).strip()
    keyboard = build_rating_keyboard(all_stories)
    return text, keyboard


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    loader  = await update.message.reply_text("⏳ Compiling digest…")
    prefs   = load_prefs()

    text, keyboard = build_all_message(prefs)
    save_prefs(prefs)

    await push(ctx.bot, chat_id, text, keyboard)
    try:
        await loader.delete()
    except Exception:
        pass


async def cmd_myprofile(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    prefs = load_prefs()
    total = prefs.get("total_ratings", 0)

    if total == 0:
        await update.message.reply_html(
            "🧠 <b>Your Preference Profile</b>\n\n"
            "No ratings yet! Use the 👍/👎 buttons on your digest stories to start building your profile.\n\n"
            "<i>Personalisation unlocks after 5 ratings.</i>"
        )
        return

    def top_items(d: dict, n: int = 5) -> tuple:
        positive = sorted([(k, v) for k, v in d.items() if v > 0], key=lambda x: x[1], reverse=True)[:n]
        negative = sorted([(k, v) for k, v in d.items() if v < 0], key=lambda x: x[1])[:n]
        return positive, negative

    liked_kw, disliked_kw   = top_items(prefs.get("keywords", {}))
    liked_src, disliked_src = top_items(prefs.get("sources", {}))
    liked_cat, disliked_cat = top_items(prefs.get("categories", {}))

    status = (
        f"🎯 <b>Personalisation active</b> ({total} ratings)"
        if total >= MIN_RATINGS
        else f"⏳ <b>Learning…</b> {MIN_RATINGS - total} more rating(s) to unlock personalisation ({total}/{MIN_RATINGS})"
    )

    lines = ["🧠 <b>Your Preference Profile</b>", "", status, ""]

    if liked_kw:
        lines.append("✅ <b>Topics you like:</b>")
        lines.append("  " + ", ".join(k for k, _ in liked_kw))
        lines.append("")

    if disliked_kw:
        lines.append("❌ <b>Topics you dislike:</b>")
        lines.append("  " + ", ".join(k for k, _ in disliked_kw))
        lines.append("")

    if liked_src:
        lines.append("✅ <b>Favourite sources:</b>")
        lines.append("  " + ", ".join(k.title() for k, _ in liked_src))
        lines.append("")

    if disliked_src:
        lines.append("❌ <b>Avoided sources:</b>")
        lines.append("  " + ", ".join(k.title() for k, _ in disliked_src))
        lines.append("")

    if liked_cat:
        lines.append("✅ <b>Favourite categories:</b>")
        lines.append("  " + ", ".join(k.title() for k, _ in liked_cat))
        lines.append("")

    if disliked_cat:
        lines.append("❌ <b>Skipped categories:</b>")
        lines.append("  " + ", ".join(k.title() for k, _ in disliked_cat))
        lines.append("")

    last = prefs.get("last_updated", "")
    if last:
        lines.append(f"<i>Last updated: {last[:16].replace('T', ' ')} MYT</i>")

    await update.message.reply_html("\n".join(lines))


# ── Scheduled: Daily Digest at 8 AM ───────────────────────────────────────────

async def daily_digest(app: Application) -> None:
    logger.info("Running daily digest…")
    tz    = pytz.timezone(TIMEZONE)
    date  = datetime.now(tz).strftime("%A, %d %B %Y")
    prefs = load_prefs()

    await push(app.bot, CHAT_ID, f"🌅 <b>Good Morning, Rodney! Your Daily Digest</b>\n📅 {date}")
    try:
        await push(app.bot, CHAT_ID, get_market_snapshot())
    except Exception as exc:
        logger.error("Market snapshot failed: %s", exc)

    for key in CATEGORIES:
        try:
            await deliver(app.bot, CHAT_ID, key, prefs)
            await asyncio.sleep(1)
        except Exception as exc:
            logger.error("Category %s failed: %s", key, exc)

    save_prefs(prefs)


# ── Scheduled: Breaking News every 30 min ─────────────────────────────────────

async def check_breaking_news(app: Application) -> None:
    global _seen_links, _breaking_initialized, _daily_alert_count, _daily_alert_date

    tz    = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    if today != _daily_alert_date:
        _daily_alert_date  = today
        _daily_alert_count = 0

    prefs            = load_prefs()
    breaking_stories: dict = {}

    for key, cat in CATEGORIES.items():
        stories = fetch_stories(cat["feeds"], limit=10)
        new = [s for s in stories if s["raw_link"] and s["raw_link"] not in _seen_links]
        for s in stories:
            if s["raw_link"]:
                _seen_links.add(s["raw_link"])
        hot = [s for s in new if is_breaking(s["raw_title"], s.get("compound", 0.0))]
        if hot:
            breaking_stories[key] = hot
            cache_stories(hot, key, prefs)

    if not _breaking_initialized:
        _breaking_initialized = True
        save_prefs(prefs)
        logger.info("Breaking-news tracker ready. %d links indexed.", len(_seen_links))
        return

    if not breaking_stories:
        return

    remaining = MAX_DAILY_ALERTS - _daily_alert_count
    if remaining <= 0:
        logger.info("Daily alert cap (%d) reached.", MAX_DAILY_ALERTS)
        return

    capped: dict = {}
    for key, stories in breaking_stories.items():
        if remaining <= 0:
            break
        take        = stories[:remaining]
        capped[key] = take
        remaining  -= len(take)

    total = sum(len(v) for v in capped.values())
    _daily_alert_count += total
    logger.info("Breaking news: %d story/stories (daily total: %d/%d).",
                total, _daily_alert_count, MAX_DAILY_ALERTS)

    await push(
        app.bot, CHAT_ID,
        f"🚨 <b>Breaking News</b> — {total} new alert{'s' if total > 1 else ''}",
    )
    for key, stories in capped.items():
        cat   = CATEGORIES[key]
        lines = [f"{cat['emoji']} <b>{cat['title']}</b>", ""]
        for s in stories[:3]:
            block = f"{s['sentiment']} <b>{s['title']}</b>\n<i>{s['source']}</i>"
            if s["summary"]:
                block += f" — {s['summary']}"
            if s["link"]:
                block += f'\n<a href="{s["link"]}">Read more →</a>'
            lines.extend([block, ""])
        lines.append("<i>Rate these alerts 👇</i>")
        keyboard = build_rating_keyboard(stories[:3])
        await push(app.bot, CHAT_ID, "\n".join(lines).strip(), keyboard)
        await asyncio.sleep(0.3)

    save_prefs(prefs)


# ── Boot ───────────────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("all",       "📰 Full digest + market"),
        BotCommand("world",     "🌍 World News"),
        BotCommand("business",  "💼 Business News"),
        BotCommand("fintech",   "💳 Fintech & Crypto"),
        BotCommand("tech",      "💻 Technology"),
        BotCommand("malaysia",  "🇲🇾 Malaysian News"),
        BotCommand("market",    "📊 Live Market Snapshot"),
        BotCommand("myprofile", "🧠 Your preference profile"),
        BotCommand("help",      "❓ Help"),
    ])
    logger.info("Commands menu registered.")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    for cmd, fn in [
        ("start",     cmd_start),
        ("help",      cmd_help),
        ("world",     cmd_world),
        ("business",  cmd_business),
        ("fintech",   cmd_fintech),
        ("tech",      cmd_tech),
        ("malaysia",  cmd_malaysia),
        ("market",    cmd_market),
        ("all",       cmd_all),
        ("myprofile", cmd_myprofile),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(handle_rating, pattern=r"^r:(like|dislike):[0-9a-f]{8}$"))

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(daily_digest,        "cron",     hour=8, minute=0,  args=[app])
    scheduler.add_job(check_breaking_news, "interval", minutes=30,        args=[app])
    scheduler.start()

    logger.info("Bot v2.1 live — personalisation enabled.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
