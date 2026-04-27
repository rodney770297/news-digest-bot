"""Market data via Stooq + on-demand chart generation."""

from __future__ import annotations

import io
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime

import pytz

logger = logging.getLogger(__name__)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")

MARKET_TICKERS = [
    ("🇲🇾 KLCI",   "^klci",   "{:,.2f}",  " pts"),
    ("💵 USD/MYR", "usdmyr",  "{:.4f}",   ""),
    ("₿  BTC",     "btcusd",  "${:,.0f}", ""),
    ("📈 S&P 500", "^spx",    "{:,.2f}",  " pts"),
    ("🥇 Gold",    "xauusd",  "${:,.2f}", "/oz"),
]


def _fetch_stooq_quote(symbol: str) -> tuple[float, float] | None:
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        rows = text.strip().splitlines()
        if len(rows) < 2:
            return None
        cols = rows[1].split(",")
        if len(cols) < 7 or "N/D" in cols[3:7]:
            return None
        return float(cols[6]), float(cols[3])
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, IndexError) as exc:
        logger.warning("Stooq quote [%s]: %s", symbol, exc)
        return None


def _fetch_stooq_history(symbol: str, days: int = 7) -> list:
    """Return up to `days` recent (date, close) pairs, oldest→newest."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        rows = text.strip().splitlines()[1:]  # skip header
        out: list = []
        for row in rows[-days:]:
            cols = row.split(",")
            if len(cols) < 5:
                continue
            try:
                out.append((cols[0], float(cols[4])))
            except ValueError:
                continue
        return out
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        logger.warning("Stooq history [%s]: %s", symbol, exc)
        return []


def get_market_snapshot() -> str:
    tz   = pytz.timezone(TIMEZONE)
    time = datetime.now(tz).strftime("%d %b %Y · %I:%M %p MYT")
    lines = [f"📊 <b>MARKET SNAPSHOT</b>", f"🕐 {time}", ""]

    for label, ticker, fmt, unit in MARKET_TICKERS:
        quote = _fetch_stooq_quote(ticker)
        if quote is None:
            lines.append(f"⚪ <b>{label}</b>: data unavailable")
            continue
        price, prev = quote
        if prev == 0:
            lines.append(f"⚪ <b>{label}</b>: {fmt.format(price)}{unit}")
            continue
        pct   = ((price - prev) / prev) * 100
        arrow = "🟢" if pct >= 0 else "🔴"
        lines.append(
            f"{arrow} <b>{label}</b>: {fmt.format(price)}{unit}"
            f"  <i>({pct:+.2f}%)</i>"
        )

    return "\n".join(lines)


def generate_market_chart() -> bytes | None:
    """Render a 5-panel sparkline grid of 7-day price history. Returns PNG bytes."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping chart.")
        return None

    fig, axes = plt.subplots(1, len(MARKET_TICKERS), figsize=(15, 3))
    fig.patch.set_facecolor("#0d0d0d")

    # Strip emoji prefix; matplotlib default fonts often don't render them.
    plain_labels = [lbl.split(" ", 1)[1] if " " in lbl else lbl for lbl, *_ in MARKET_TICKERS]

    for ax, plain, (_, ticker, fmt, unit) in zip(axes, plain_labels, MARKET_TICKERS):
        history = _fetch_stooq_history(ticker, days=7)
        ax.set_facecolor("#0d0d0d")
        ax.tick_params(colors="#888", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#333")

        if not history:
            ax.text(0.5, 0.5, "no data", ha="center", va="center",
                    color="#888", transform=ax.transAxes)
            ax.set_title(plain, color="#eee", fontsize=10)
            ax.set_xticks([])
            ax.set_yticks([])
            continue

        prices = [p for _, p in history]
        color  = "#4ade80" if prices[-1] >= prices[0] else "#f87171"
        ax.plot(prices, color=color, linewidth=2)
        ax.fill_between(range(len(prices)), prices, min(prices),
                        alpha=0.15, color=color)
        change = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] else 0
        ax.set_title(
            f"{plain}\n{fmt.format(prices[-1])}{unit}  ({change:+.2f}% 7d)",
            color="#eee", fontsize=9,
        )
        ax.set_xticks([])
        ax.set_yticks([])

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor="#0d0d0d")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
