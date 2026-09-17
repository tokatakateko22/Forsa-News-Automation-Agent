"""
app/graph/state.py
───────────────────
LangGraph AgentState — the shared state object passed between all pipeline nodes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, TypedDict

from app.models.article import Article, ArticleClassification
from app.models.event import NewsEvent, EventSummary


class RunStats(TypedDict):
    """Counters logged to the workflow_runs table after completion."""
    articles_collected: int
    articles_preprocessed: int
    articles_relevant: int
    articles_deduplicated: int
    articles_verified: int
    events_detected: int
    events_sent: int
    summaries_generated: int
    email_sent: bool
    errors: list[str]


class AgentState(TypedDict):
    """
    Complete pipeline state — flows through every node unchanged except
    for the specific fields that node is responsible for populating.

    Fields are populated progressively:
      collect_news      → raw_articles
      preprocess_news   → clean_articles
      classify_articles → classifications, relevant_articles
      deduplicate       → events
      verify_sources    → events (verification_status updated)
      filter_important  → important_events
      summarize_news    → events (summaries populated)
      format_email      → email_subject, email_html, email_plain
      send_email        → email_sent
    """
    # ── Run metadata ──────────────────────────────────────────────────────────
    run_id: str
    started_at: datetime
    collection_start: datetime      # Start of the news retrieval window
    collection_end: datetime        # End of the news retrieval window (now)
    ignore_already_sent: bool       # Bypass DB sent-events check (for testing)
    force_search_fallback: bool     # Force fallback search mechanism (for testing)

    # ── Collection ────────────────────────────────────────────────────────────
    raw_articles: list[Article]

    # ── Preprocessing ─────────────────────────────────────────────────────────
    clean_articles: list[Article]

    # ── Classification ────────────────────────────────────────────────────────
    # article_id -> ArticleClassification
    classifications: dict[str, ArticleClassification]
    relevant_articles: list[Article]

    # ── Deduplication ─────────────────────────────────────────────────────────
    events: list[NewsEvent]

    # ── Importance Filter ─────────────────────────────────────────────────────
    important_events: list[NewsEvent]

    # ── Email content ─────────────────────────────────────────────────────────
    email_subject: str
    email_html: str
    email_plain: str
    email_sent: bool

    # ── Errors & stats ────────────────────────────────────────────────────────
    errors: list[str]
    stats: RunStats
