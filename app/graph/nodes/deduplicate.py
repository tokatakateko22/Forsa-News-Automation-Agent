"""
app/graph/nodes/deduplicate.py
───────────────────────────────
Node 4: deduplicate_articles
Groups articles covering the same event into NewsEvent objects.
"""
from __future__ import annotations

import structlog

from app.graph.state import AgentState
from app.models.event import NewsEvent
from app.services.deduplication import DeduplicationService
from app.services.embeddings import get_embeddings_batch

log = structlog.get_logger(__name__)


async def deduplicate_articles(state: AgentState) -> AgentState:
    """
    LangGraph node: deduplicate_articles
    Uses multi-signal deduplication to group articles into events.
    Optionally uses semantic embeddings as the final signal.
    """
    articles = state["relevant_articles"]
    classifications = state["classifications"]

    log.info("node.deduplicate.start", article_count=len(articles))

    # Compute embeddings for semantic similarity (final dedup signal)
    # Only compute if there are enough articles to justify the cost
    embeddings: dict[str, list[float]] = {}
    if len(articles) >= 3:
        try:
            text_pairs = [
                (a.article_id, f"{a.title}. {(a.content or '')[:500]}")
                for a in articles
            ]
            embeddings = await get_embeddings_batch(text_pairs)
            log.info("node.deduplicate.embeddings", count=len(embeddings))
        except Exception as exc:
            log.warning("node.deduplicate.embeddings_failed", error=str(exc))

    service = DeduplicationService(embeddings=embeddings)
    events: list[NewsEvent] = service.deduplicate(articles, classifications)

    log.info(
        "node.deduplicate.done",
        input_articles=len(articles),
        output_events=len(events),
    )

    stats = dict(state.get("stats", {}))
    stats["events_detected"] = len(events)
    stats["articles_deduplicated"] = len(articles) - len(events)

    return {
        **state,
        "events": events,
        "stats": stats,
    }
