"""Feed fetching — RSS + CoinGecko crypto stories."""

from __future__ import annotations

import html
import json
import logging
import re
import socket
import urllib.error
import urllib.request

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from formatting import clean_summary, one_liner, strip_html

logger = logging.getLogger(__name__)
_sia   = SentimentIntensityAnalyzer()

FEED_TIMEOUT = 8  # seconds

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CATEGORIES = {
    "world": {
        "emoji": "🌍", "title": "WORLD NEWS",
        "feeds": [
            ("BBC World",   "https://feeds.bbci.co.uk/news/world/rss.xml"),
            ("Al Jazeera",  "https://www.aljazeera.com/xml/rss/all.xml"),
            ("NPR World",   "https://feeds.npr.org/1004/rss.xml"),
        ],
    },
    "business": {
        "emoji": "💼", "title": "BUSINESS NEWS",
        "feeds": [
            ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
            ("BBC Business",  "https://feeds.bbci.co.uk/news/business/rss.xml"),
            ("CNBC",          "https://www.cnbc.com/id/10001147/device/rss/rss.html"),
        ],
    },
    "fintech": {
        "emoji": "💳", "title": "FINTECH & CRYPTO",
        "feeds": [
            ("PYMNTS",     "https://www.pymnts.com/feed/"),
            ("The Block",  "https://www.theblock.co/rss.xml"),
            ("Decrypt",    "https://decrypt.co/feed"),
        ],
        "include_coingecko": True,  # synthesize crypto market-move stories
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
            ("NST",            "https://www.nst.com.my/feed"),
            ("Google News MY", "https://news.google.com/rss/search?q=malaysia&hl=en-MY&gl=MY&ceid=MY:en"),
            ("Bursa/KLCI",     "https://news.google.com/rss/search?q=KLCI+OR+ringgit+OR+bursa+malaysia&hl=en-MY&gl=MY&ceid=MY:en"),
        ],
        "geo_keywords": {
            "malaysia", "malaysian", "kuala lumpur", "klci", "ringgit",
            "bursa", "putrajaya", "sabah", "sarawak", "penang", "johor",
            "perak", "selangor", "petaling jaya", "myr", "kl ",
            "mahathir", "anwar", "najib", "umno", "pakatan",
            "petronas", "khazanah", "tabung haji", "epf", "kwsp",
            "bank negara", "mida", "mdec",
        },
        "english_only": True,
    },
}

MALAY_INDICATORS = {
    "yang", "kepada", "bahawa", "kerana", "daripada", "tidak", "akan",
    "telah", "dengan", "untuk", "bagi", "apabila", "seperti", "antara",
    "lebih", "boleh", "dalam", "rakyat", "kerajaan", "perdana", "menteri",
    "perikatan", "nasional", "parti", "ahli", "parlimen", "dewan",
    "berkata", "semua", "mereka", "adalah", "jika", "hanya",
    "satu", "dua", "tiga", "empat", "lima",
}

_punct_re = re.compile(r"[^\w\s]")


def _make_dedup_key(title: str) -> str:
    clean = _punct_re.sub("", title.lower())
    words = [w for w in clean.split() if len(w) > 2][:8]
    return " ".join(words)


def _parse_feed(url: str):
    """feedparser with explicit timeout. Returns parsed feed or empty stub."""
    try:
        # feedparser respects socket default timeout for its internal urllib calls
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_TIMEOUT)
        try:
            return feedparser.parse(url, request_headers={"User-Agent": UA})
        finally:
            socket.setdefaulttimeout(old_timeout)
    except Exception as exc:
        logger.warning("Feed parse [%s]: %s", url, exc)

        class _Empty:
            entries: list = []
        return _Empty()


def _sentiment(text: str) -> tuple[float, str]:
    compound = _sia.polarity_scores(text)["compound"]
    return compound, "📈" if compound >= 0.05 else "📉" if compound <= -0.05 else "⚖️"


def fetch_stories(
    feeds: list,
    limit: int = 5,
    geo_keywords: set | None = None,
    english_only: bool = False,
) -> list:
    """Synchronous; call via asyncio.to_thread from async context."""
    all_stories: list = []
    seen_titles: set  = set()
    seen_links:  set  = set()

    for source, url in feeds:
        feed = _parse_feed(url)
        for entry in feed.entries[:12]:
            title = strip_html(entry.get("title", "")).strip()
            if not title:
                continue

            if english_only:
                title_words = set(re.sub(r"[^a-z ]", " ", title.lower()).split())
                if title_words & MALAY_INDICATORS:
                    continue

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
            oneline = one_liner(raw)

            if geo_keywords:
                haystack = (title + " " + strip_html(raw)).lower()
                if not any(kw in haystack for kw in geo_keywords):
                    continue

            compound, _emoji = _sentiment(title + " " + summary)

            all_stories.append({
                "source":    source,
                "title":     html.escape(title),
                "summary":   html.escape(summary),
                "oneline":   html.escape(oneline),
                "link":      link,
                "compound":  compound,
                "raw_title": title,
                "raw_link":  link,
            })

    return all_stories[:limit]


# ── CoinGecko crypto stories ─────────────────────────────────────────────────

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=20&page=1"
    "&price_change_percentage=24h"
)


def fetch_coingecko_stories(limit: int = 2) -> list:
    """Synthesize story dicts from CoinGecko top-mover data. Sync; call via to_thread."""
    try:
        req = urllib.request.Request(COINGECKO_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        logger.warning("CoinGecko: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    movers = sorted(
        (c for c in data if c.get("price_change_percentage_24h") is not None),
        key=lambda c: abs(c["price_change_percentage_24h"]),
        reverse=True,
    )[:limit]

    stories = []
    for c in movers:
        sym    = c["symbol"].upper()
        name   = c["name"]
        price  = c["current_price"]
        pct    = c["price_change_percentage_24h"]
        arrow  = "▲" if pct >= 0 else "▼"
        title  = f"{name} ({sym}) {arrow} {pct:+.1f}% in 24h to ${price:,.2f}"
        oneline = (
            f"Market cap rank #{c.get('market_cap_rank', '?')}; "
            f"24h volume ${c.get('total_volume', 0):,.0f}."
        )
        link = f"https://www.coingecko.com/en/coins/{c['id']}"
        stories.append({
            "source":    "CoinGecko",
            "title":     html.escape(title),
            "summary":   html.escape(oneline),
            "oneline":   html.escape(oneline),
            "link":      link,
            "compound":  max(-1.0, min(1.0, pct / 10.0)),
            "raw_title": title,
            "raw_link":  link,
        })
    return stories
