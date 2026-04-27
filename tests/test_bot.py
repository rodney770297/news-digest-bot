"""Pure-function tests. Run: python -m pytest tests/ -q"""

from __future__ import annotations

from formatting import clean_summary, format_story_block, one_liner, strip_html
from breaking import is_breaking


# ── formatting ──────────────────────────────────────────────────────────

def test_strip_html_removes_tags():
    assert strip_html("<p>hello <b>world</b></p>") == "hello  world"
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_one_liner_takes_first_sentence():
    raw = "First sentence here. Second sentence here. Third one."
    assert one_liner(raw) == "First sentence here."


def test_one_liner_truncates_long_text():
    raw = "x " * 200
    out = one_liner(raw, max_chars=50)
    assert len(out) <= 51
    assert out.endswith("…")


def test_clean_summary_takes_two_sentences():
    assert clean_summary("One. Two. Three. Four.") == "One. Two."


def test_format_story_block_escapes_link_url():
    s = {
        "title":     "Some title",
        "oneline":   "Description",
        "source":    "Source",
        "link":      'https://x.com/?q="evil"',
        "raw_title": "Some title",
    }
    block = format_story_block(0, "world", {"emoji": "🌍"}, s)
    assert '"evil"' not in block
    assert "&quot;evil&quot;" in block


def test_format_story_block_no_sentiment_emoji():
    s = {
        "title": "Test", "oneline": "desc", "source": "Src",
        "link":  "https://x.com", "raw_title": "Test", "compound": 0.9,
    }
    block = format_story_block(0, "business", {"emoji": "💼"}, s)
    assert "📈" not in block and "📉" not in block and "⚖️" not in block


def test_format_story_block_includes_category_tag():
    s = {
        "title": "Headline", "oneline": "Desc",
        "source": "Src", "link": "", "raw_title": "Headline",
    }
    block = format_story_block(0, "malaysia", {"emoji": "🇲🇾"}, s)
    assert "[MALAYSIA]" in block


# ── breaking-news heuristic ──────────────────────────────────────────────

def test_is_breaking_tier1_keyword():
    assert is_breaking("Major bank breach exposes 10M users")
    assert is_breaking("BREAKING: ceasefire announced")


def test_is_breaking_requires_sentiment_for_tier2():
    # tier 2 alone: no
    assert not is_breaking("Company announces merger plans", compound=0.0)
    # tier 2 + strong sentiment: yes
    assert is_breaking("Markets plunge dramatically on rate cut fears", compound=-0.6)


def test_is_breaking_ignores_neutral_news():
    assert not is_breaking("Quarterly earnings report released today")
