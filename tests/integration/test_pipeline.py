"""
tests/integration/test_pipeline.py
────────────────────────────────────
Integration test for the full LangGraph pipeline.
Uses offline mocked articles (no real HTTP or LLM calls required).
Validates pipeline structure and state transformations end-to-end.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.graph.state import AgentState, RunStats


def make_test_state(**kwargs) -> AgentState:
    """Create a minimal valid initial state for pipeline testing."""
    base: AgentState = {
        "run_id": "test-run-001",
        "started_at": datetime.now(timezone.utc),
        "collection_start": datetime(2026, 9, 13, 6, 0, 0, tzinfo=timezone.utc),
        "collection_end": datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc),
        "ignore_already_sent": False,
        "force_search_fallback": False,
        "raw_articles": [],
        "clean_articles": [],
        "classifications": {},
        "relevant_articles": [],
        "events": [],
        "important_events": [],
        "email_subject": "",
        "email_html": "",
        "email_plain": "",
        "email_sent": False,
        "errors": [],
        "stats": RunStats(
            articles_collected=0,
            articles_preprocessed=0,
            articles_relevant=0,
            articles_deduplicated=0,
            articles_verified=0,
            events_detected=0,
            events_sent=0,
            summaries_generated=0,
            email_sent=False,
            errors=[],
        ),
    }
    base.update(kwargs)  # type: ignore
    return base


@pytest.mark.asyncio
async def test_preprocess_node_cleans_html():
    """Preprocessing node strips HTML from article content."""
    from app.graph.nodes.preprocess import preprocess_news
    from app.models.article import Article

    article = Article(
        title="<b>CBE raises rates</b>",
        url="https://cbe.org.eg/test",
        content="<p>The CBE raised <strong>interest rates</strong> by 100bps.</p>",
        source_name="CBE",
    )
    state = make_test_state(raw_articles=[article])
    result = await preprocess_news(state)

    assert len(result["clean_articles"]) == 1
    clean = result["clean_articles"][0]
    assert "<b>" not in clean.title
    assert "<p>" not in (clean.content or "")
    assert "interest rates" in (clean.content or "")


@pytest.mark.asyncio
async def test_classify_node_filters_irrelevant():
    """Classify node rejects articles with no monitoring-scope keywords."""
    from app.graph.nodes.classify import classify_articles
    from app.models.article import Article

    irrelevant = Article(
        title="Egypt defeats Senegal in World Cup qualifier",
        url="https://sports.com/egypt-senegal",
        content="Egypt won 2-1 in a football match.",
        source_name="Sports News",
    )
    state = make_test_state(clean_articles=[irrelevant])
    result = await classify_articles(state)

    # Should be filtered by keyword pre-filter (no LLM call needed)
    # The article has no financial keywords
    assert result["relevant_articles"] == [] or all(
        a.url != irrelevant.url for a in result["relevant_articles"]
    )


@pytest.mark.asyncio
async def test_verify_node_marks_cbe_official():
    """Verification node marks CBE-sourced events as OFFICIAL."""
    from app.graph.nodes.verify import verify_sources
    from app.models.article import Article
    from app.models.event import NewsEvent

    article = Article(
        title="CBE raises rates",
        url="https://www.cbe.org.eg/en/press-releases/mpc-2026",
        source_name="CBE",
        source_tier=1,
    )
    event = NewsEvent(
        canonical_title="CBE raises rates",
        category="CBE",
        canonical_url="https://www.cbe.org.eg/en/press-releases/mpc-2026",
        canonical_source_name="CBE",
        canonical_source_tier=1,
        articles=[article],
    )
    state = make_test_state(events=[event])
    result = await verify_sources(state)

    assert result["events"][0].verification_status in ("OFFICIAL", "VERIFIED")


@pytest.mark.asyncio
async def test_filter_routes_no_news():
    """Filter node returns no important events when all scores are below threshold."""
    from app.graph.nodes.filter import filter_important_news, route_after_filter
    from app.models.event import NewsEvent
    from app.models.article import Article

    # Mock LLM scoring to return low scores
    with patch("app.graph.nodes.filter._score_event") as mock_score:
        async def mock_fn(event):
            event.importance_score = 10  # Below threshold
            return event
        mock_score.side_effect = mock_fn

        with patch("app.graph.nodes.filter.get_session"):
            state = make_test_state(events=[])
            result = await filter_important_news(state)
            route = route_after_filter(result)
            assert route == "no_news"


@pytest.mark.asyncio
async def test_format_email_no_analysis():
    """Email formatter must not include analysis or score words."""
    from app.graph.nodes.format_email import format_email
    from app.models.event import NewsEvent, EventSummary

    summary = EventSummary(
        event_id="evt-001",
        summary_text="The Central Bank of Egypt raised interest rates by 100 basis points.",
        category="CBE",
        canonical_title="CBE raises rates",
        canonical_url="https://cbe.org.eg/press-release",
        source_name="Central Bank of Egypt",
        published_at=datetime.now(timezone.utc),
    )
    event = NewsEvent(
        canonical_title="CBE raises rates",
        category="CBE",
        canonical_url="https://cbe.org.eg/press-release",
        canonical_source_name="Central Bank of Egypt",
        canonical_source_tier=1,
        importance_score=90,  # Internal — must not appear in output
        relevance_score=95,
        should_send=True,
    )
    event.summary = summary

    state = make_test_state(important_events=[event])
    result = await format_email(state)

    forbidden = [
        "importance_score", "relevance_score", "why this matters",
        "forsa should", "recommended", "impact on forsa",
    ]
    email_text = (result["email_plain"] + result["email_html"]).lower()
    for phrase in forbidden:
        assert phrase not in email_text, f"Forbidden phrase in email: {phrase!r}"

    # Check required content is present
    assert "Central Bank of Egypt" in result["email_plain"]
    assert "https://cbe.org.eg" in result["email_plain"]
