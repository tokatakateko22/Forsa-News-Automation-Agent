"""
app/graph/nodes/summarize.py
─────────────────────────────
Node 7: summarize_news
Generates factual summaries ONLY for events that passed the importance filter.
Uses Gemini Pro (quality model). Strictly enforces no-analysis rules.
"""
from __future__ import annotations

import asyncio
import re

import structlog

from app.graph.state import AgentState
from app.models.event import EventSummary, NewsEvent
from app.services import llm
from app.services.spelling import normalize_brand_spellings

log = structlog.get_logger(__name__)

_SEMAPHORE = asyncio.Semaphore(1)  # Sequential for free-tier rate limits


_STUB_PHRASES = [
    "no_substance",
    "contains only the title",
    "without disclosing any concrete figures",
    "no specific figures",
    "no specific dates, rates, or institutional decisions",
    "metadata and date of this listing committee",
    "provides only the metadata",
]


async def _summarize_event(event: NewsEvent) -> NewsEvent:
    """Generate and attach a factual summary to the event."""
    await asyncio.sleep(4.0)  # Safe pacing between API calls
    try:
        # Use the best available content, preferring English content when available
        content = ""
        for article in event.articles:
            if getattr(article, "is_english", False) and article.content and len(article.content) > len(content):
                content = article.content
        if not content or len(content) < 80:
            for article in event.articles:
                if article.content and len(article.content) > len(content):
                    content = article.content
        if not content:
            content = event.canonical_title

        summary_text = await llm.summarize_article(
            title=event.canonical_title,
            source=event.canonical_source_name,
            content=content,
        )

        # Check if LLM rejected it as a stub, returned garbled output, or leaked Arabic/checklists
        summary_lower = summary_text.lower()
        has_arabic = bool(re.search(r"[\u0600-\u06FF]", summary_text))
        is_malformed = (
            summary_text.startswith(")?*")
            or summary_text.startswith(")")
            or summary_text.startswith("?*")
            or "?* yes" in summary_lower
            or "?* no" in summary_lower
            or "professional english? yes" in summary_lower
            or "concrete facts? yes" in summary_lower
        )
        if (
            len(summary_text) < 50
            or is_malformed
            or has_arabic
            or any(phrase in summary_lower for phrase in _STUB_PHRASES)
        ):
            log.warning(
                "summarize.rejected_invalid_or_stub",
                event_id=event.event_id,
                title=event.canonical_title[:50],
                has_arabic=has_arabic,
                is_malformed=is_malformed,
                length=len(summary_text),
            )
            event.summary = None
            return event

        competitor_match = (
            event.classification.competitor_match
            if event.classification else None
        )
        summary_text = normalize_brand_spellings(summary_text, competitor_hint=competitor_match)

        event.summary = EventSummary(
            event_id=event.event_id,
            summary_text=summary_text,
            category=event.category,
            subcategory=event.subcategory,
            canonical_title=normalize_brand_spellings(event.canonical_title, competitor_hint=competitor_match),
            canonical_url=event.canonical_url,
            source_name=event.canonical_source_name,
            published_at=event.event_date,
            competitor_name=competitor_match,
        )
    except Exception as exc:
        log.error(
            "summarize.failed",
            event_id=event.event_id,
            error=str(exc),
        )
        event.summary = None
    return event


async def summarize_news(state: AgentState) -> AgentState:
    """
    LangGraph node: summarize_news
    Calls Gemini for each important event sequentially with pacing.
    Only events in important_events are summarized — stubs and contentless events are dropped.
    """
    events = state["important_events"]
    log.info("node.summarize.start", event_count=len(events))

    summarized: list[NewsEvent] = []
    for e in events:
        s = await _summarize_event(e)
        if s.summary and len(s.summary.summary_text) > 30:
            summarized.append(s)
        else:
            log.info("node.summarize.dropped_stub", title=e.canonical_title[:60])

    log.info("node.summarize.done", summaries_generated=len(summarized))

    stats = dict(state.get("stats", {}))
    stats["summaries_generated"] = len(summarized)

    return {
        **state,
        "important_events": summarized,
        "stats": stats,
    }
