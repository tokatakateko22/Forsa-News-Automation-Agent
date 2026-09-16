"""
app/graph/nodes/collect.py
───────────────────────────
Node 1: collect_news
Runs all collectors in parallel, isolates failures per source,
persists raw articles to DB, and deduplicates by URL before passing downstream.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog

from app.collectors.base import NewsSourceCollector
from app.collectors.cbe import CBECollector
from app.collectors.fra import FRACollector
from app.collectors.rss import RSSCollector
from app.collectors.search import SerpAPICollector, SerpAPICompetitorCollector
from app.config import settings
from app.database.connection import get_session
from app.database.models import Article as ArticleORM
from app.database.repositories import (
    ArticleRepository,
    CompetitorRepository,
    SourceRepository,
)
from app.graph.state import AgentState
from app.models.article import Article

log = structlog.get_logger(__name__)


async def _build_collectors(
    start_time: datetime,
    end_time: datetime,
) -> list[NewsSourceCollector]:
    """
    Dynamically build collector instances from the database configuration.
    Competitors and RSS sources are read from DB — no hard-coding.
    """
    collectors: list[NewsSourceCollector] = []

    # ── Tier 1: Official scrapers ─────────────────────────────────────────────
    collectors.append(CBECollector(timeout=settings.http_timeout_seconds))
    collectors.append(FRACollector(timeout=settings.http_timeout_seconds))

    # ── Tier 2: Category search via SerpAPI ───────────────────────────────────
    collectors.append(SerpAPICollector(timeout=settings.http_timeout_seconds))

    # ── Tier 2: RSS feeds from DB ─────────────────────────────────────────────
    async with get_session() as session:
        source_repo = SourceRepository(session)
        sources = await source_repo.get_active_sources()

        rss_sources = [s for s in sources if s.source_type == "rss"]
        for src in rss_sources:
            collectors.append(
                RSSCollector(
                    feed_url=src.url,
                    source_name=src.name,
                    source_tier=src.tier,
                    source_id=src.id,
                    language=src.language,
                    timeout=settings.http_timeout_seconds,
                )
            )

        # ── Competitor-specific searches (Priority 1 direct competitors) ──────
        competitor_repo = CompetitorRepository(session)
        competitors = await competitor_repo.get_active_competitors()
        p1_competitors = [c for c in competitors if c.priority == 1]

    for comp in p1_competitors:
        collectors.append(
            SerpAPICompetitorCollector(
                competitor_name=comp.name,
                aliases=comp.aliases or [],
                timeout=settings.http_timeout_seconds,
            )
        )

    return collectors


async def collect_news(state: AgentState) -> AgentState:
    """
    LangGraph node: collect_news
    Runs all collectors concurrently; one failed source does not abort the run.
    Persists new articles to DB. Returns deduplicated list of Article objects.
    """
    log.info(
        "node.collect_news.start",
        run_id=state["run_id"],
        window_start=state["collection_start"].isoformat(),
        window_end=state["collection_end"].isoformat(),
    )

    start_time = state["collection_start"]
    end_time = state["collection_end"]
    errors: list[str] = list(state.get("errors", []))

    try:
        collectors = await _build_collectors(start_time, end_time)
    except Exception as exc:
        msg = f"Failed to build collectors: {exc}"
        log.error("node.collect_news.build_failed", error=msg)
        errors.append(msg)
        return {**state, "raw_articles": [], "errors": errors}

    # Run all collectors concurrently
    tasks = [c.safe_fetch(start_time, end_time) for c in collectors]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    all_articles: list[Article] = []
    for batch in results:
        all_articles.extend(batch)

    log.info(
        "node.collect_news.raw_total",
        count=len(all_articles),
    )

    # URL-level deduplication before storing
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    unique_articles: list[Article] = []

    for article in all_articles:
        if article.url in seen_urls:
            continue
        if article.content_hash and article.content_hash in seen_hashes:
            continue
        seen_urls.add(article.url)
        if article.content_hash:
            seen_hashes.add(article.content_hash)
        unique_articles.append(article)

    log.info(
        "node.collect_news.unique",
        count=len(unique_articles),
        duplicates_removed=len(all_articles) - len(unique_articles),
    )

    # Persist to DB
    try:
        await _persist_articles(unique_articles)
    except Exception as exc:
        msg = f"DB persist error: {exc}"
        log.error("node.collect_news.db_error", error=msg)
        errors.append(msg)
        # Don't abort — continue with in-memory articles

    stats = dict(state.get("stats", {}))
    stats["articles_collected"] = len(unique_articles)

    return {
        **state,
        "raw_articles": unique_articles,
        "errors": errors,
        "stats": stats,
    }


async def _persist_articles(articles: list[Article]) -> None:
    """Save articles to the database. If already existing, sync in-memory article_id."""
    if not articles:
        return
    async with get_session() as session:
        repo = ArticleRepository(session)
        urls = [a.url for a in articles if a.url]
        existing_map = await repo.get_existing_url_map(urls)

        for article in articles:
            if article.url in existing_map:
                article.article_id = str(existing_map[article.url])
                continue
            orm_obj = ArticleORM(
                id=uuid.UUID(article.article_id),
                title=article.title,
                url=article.url,
                source_id=article.source_id,
                source_name=article.source_name,
                published_at=article.published_at,
                retrieved_at=article.retrieved_at,
                content=article.content,
                content_hash=article.content_hash,
                language=article.language,
            )
            session.add(orm_obj)
