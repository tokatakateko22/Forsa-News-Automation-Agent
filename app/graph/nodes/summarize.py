"""
app/graph/nodes/summarize.py
─────────────────────────────
Node 7: summarize_news
Generates factual summaries ONLY for events that passed the importance filter.
Uses Gemini Pro (quality model). Strictly enforces no-analysis rules.
"""
from __future__ import annotations

import asyncio

import structlog

from app.graph.state import AgentState
from app.models.event import EventSummary, NewsEvent
from app.services import llm

log = structlog.get_logger(__name__)

_SEMAPHORE = asyncio.Semaphore(1)  # Sequential for free-tier rate limits


async def _summarize_event(event: NewsEvent) -> NewsEvent:
    """Generate and attach a factual summary to the event."""
    await asyncio.sleep(4.0)  # Safe pacing between API calls
    try:
        # Use the best available content
        content = ""
        for article in event.articles:
            if article.content and len(article.content) > len(content):
                content = article.content
        if not content:
            content = event.canonical_title

        summary_text = await llm.summarize_article(
            title=event.canonical_title,
            content=content,
            source=event.canonical_source_name,
        )

        event.summary = EventSummary(
            event_id=event.event_id,
            summary_text=summary_text,
            category=event.category,
            subcategory=event.subcategory,
            canonical_title=event.canonical_title,
            canonical_url=event.canonical_url,
            source_name=event.canonical_source_name,
            published_at=event.event_date,
            competitor_name=(
                event.classification.competitor_match
                if event.classification else None
            ),
        )
    except Exception as exc:
        log.error(
            "summarize.failed",
            event_id=event.event_id,
            error=str(exc),
        )
        # Fallback: use the title as summary
        event.summary = EventSummary(
            event_id=event.event_id,
            summary_text=event.canonical_title,
            category=event.category,
            subcategory=event.subcategory,
            canonical_title=event.canonical_title,
            canonical_url=event.canonical_url,
            source_name=event.canonical_source_name,
            published_at=event.event_date,
        )
    return event


async def summarize_news(state: AgentState) -> AgentState:
    """
    LangGraph node: summarize_news
    Calls Gemini for each important event sequentially with pacing.
    Only events in important_events are summarized — never irrelevant ones.
    """
    events = state["important_events"]
    log.info("node.summarize.start", event_count=len(events))

    summarized: list[NewsEvent] = []
    for e in events:
        s = await _summarize_event(e)
        summarized.append(s)

    successful = sum(1 for e in summarized if e.summary and len(e.summary.summary_text) > 10)
    log.info("node.summarize.done", summaries_generated=successful)

    stats = dict(state.get("stats", {}))
    stats["summaries_generated"] = successful

    return {
        **state,
        "important_events": list(summarized),
        "stats": stats,
    }
