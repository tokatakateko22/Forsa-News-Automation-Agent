"""
tests/unit/test_email_format.py
────────────────────────────────
Unit tests for email formatting.
Key assertions:
  - Email contains factual content (title, source, URL, date)
  - Email does NOT contain analysis, scores, recommendations, or AI labels
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.graph.nodes.format_email import (
    _event_plain_block,
    _event_html_block,
    _build_email,
    _build_no_news_email,
    get_brief_descriptor,
    _build_subject,
    CATEGORY_ORDER,
)
from app.models.event import NewsEvent, EventSummary

FORBIDDEN_PHRASES = [
    "why this matters",
    "impact on forsa",
    "forsa should",
    "recommended action",
    "strategic implication",
    "importance score",
    "relevance score",
    "confidence",
    "ai ",
    "llm",
    "we recommend",
    "you should",
    "predicted",
    "prediction",
    "investment advice",
]


def make_event(
    title: str = "FRA issues new consumer finance supervisory manual",
    category: str = "FRA",
    source: str = "Financial Regulatory Authority",
    url: str = "https://fra.gov.eg/press-release",
) -> NewsEvent:
    event = NewsEvent(
        canonical_title=title,
        category=category,
        canonical_url=url,
        canonical_source_name=source,
        canonical_source_tier=1,
        importance_score=85,  # Internal — must NOT appear in output
        relevance_score=90,   # Internal — must NOT appear in output
        should_send=True,
    )
    event.summary = EventSummary(
        event_id=event.event_id,
        summary_text="The Financial Regulatory Authority issued new supervisory rules for consumer finance companies.",
        category=category,
        canonical_title=title,
        canonical_url=url,
        source_name=source,
        published_at=datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc),
    )
    return event


class TestCEOEmailContent:
    def test_contains_summary_text(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "Financial Regulatory Authority issued new supervisory" in block

    def test_contains_source_name(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "Financial Regulatory Authority" in block

    def test_contains_url(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "https://fra.gov.eg/press-release" in block

    def test_contains_date(self):
        event = make_event()
        block = _event_plain_block(event)
        assert "2026" in block or "Sep" in block

    def test_contains_category_emoji(self):
        event = make_event(category="FRA")
        block = _event_plain_block(event)
        assert "🔴" in block


class TestCEOEmailForbiddenContent:
    """
    Strictly verify that internal data never leaks to the CEO.
    """

    def test_no_importance_score_in_plain(self):
        event = make_event()
        block = _event_plain_block(event)
        for phrase in FORBIDDEN_PHRASES:
            assert phrase.lower() not in block.lower(), \
                f"Forbidden phrase found in plain email: {phrase!r}"

    def test_no_importance_score_in_html(self):
        event = make_event()
        block = _event_html_block(event)
        # Score numbers alone can't be forbidden (they appear in dates etc.)
        # But "importance_score" or "relevance_score" as strings must not appear
        assert "importance_score" not in block
        assert "relevance_score" not in block
        assert "85" not in block.replace("2026", "").replace("100", "")  # score values

    def test_no_analysis_phrases(self):
        event = make_event()
        plain = _event_plain_block(event)
        html = _event_html_block(event)
        combined = (plain + html).lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in combined, \
                f"Forbidden phrase found: {phrase!r}"


class TestCategoryOrdering:
    def test_fra_before_economy(self):
        assert CATEGORY_ORDER.index("FRA") < CATEGORY_ORDER.index("Economy")

    def test_fra_before_fintech(self):
        assert CATEGORY_ORDER.index("FRA") < CATEGORY_ORDER.index("FinTech")

    def test_critical_categories_first(self):
        critical = ["FRA", "Consumer Finance"]
        non_critical = ["Economy", "Other"]
        for c in critical:
            for nc in non_critical:
                assert CATEGORY_ORDER.index(c) < CATEGORY_ORDER.index(nc)


class TestNoNewsEmail:
    def test_no_news_subject_format(self):
        subject, html, plain = _build_no_news_email("13 September 2026", days=1)
        assert subject == "Consumer Finance Intelligence | Daily Brief — No Significant News"

    def test_no_news_body_factual(self):
        _, _, plain = _build_no_news_email("13 September 2026", days=1)
        assert "No significant" in plain
        # Must NOT contain fake news or analysis
        assert "CBE" not in plain
        assert "interest rate" not in plain
        assert "Forsa Financial Market News" not in plain

    def test_full_email_structure(self):
        events = [make_event()]
        subject, html, plain = _build_email(events, "13 September 2026", days=1)
        # Header block must be completely removed
        assert "Forsa Financial Market News" not in plain
        assert "Forsa Financial Market News" not in html
        assert subject == "Consumer Finance Intelligence | Daily Brief"
        assert "Financial Regulatory Authority" in plain
        assert "Financial Regulatory Authority" in html
        # Plain text must start directly with the first section divider
        assert plain.startswith("═" * 54)


class TestDynamicBriefSubject:
    def test_daily_brief_1_day(self):
        assert get_brief_descriptor(1) == "Daily Brief"
        subject = _build_subject(days=1)
        assert subject == "Consumer Finance Intelligence | Daily Brief"

    def test_weekly_brief_7_days(self):
        assert get_brief_descriptor(7) == "Weekly Brief"
        subject = _build_subject(days=7)
        assert subject == "Consumer Finance Intelligence | Weekly Brief"

    def test_monthly_brief_30_days(self):
        assert get_brief_descriptor(30) == "Monthly Brief"
        subject = _build_subject(days=30)
        assert subject == "Consumer Finance Intelligence | Monthly Brief"

    def test_biweekly_brief_14_days(self):
        assert get_brief_descriptor(14) == "Bi-Weekly Brief"
        subject = _build_subject(days=14)
        assert subject == "Consumer Finance Intelligence | Bi-Weekly Brief"

    def test_custom_days_fallback(self):
        assert get_brief_descriptor(3) == "3-Day Brief"
        assert _build_subject(days=3) == "Consumer Finance Intelligence | 3-Day Brief"
        assert get_brief_descriptor(10) == "10-Day Brief"
        assert _build_subject(days=10) == "Consumer Finance Intelligence | 10-Day Brief"

    def test_build_email_uses_days(self):
        events = [make_event()]
        s1, _, _ = _build_email(events, days=1)
        assert s1 == "Consumer Finance Intelligence | Daily Brief"

        s7, _, _ = _build_email(events, days=7)
        assert s7 == "Consumer Finance Intelligence | Weekly Brief"

        s30, _, _ = _build_email(events, days=30)
        assert s30 == "Consumer Finance Intelligence | Monthly Brief"
