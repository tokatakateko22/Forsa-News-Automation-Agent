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
from app.search import SearchManager, deduplicate_articles

log = structlog.get_logger(__name__)


async def collect_news(state: AgentState) -> AgentState:
    """
    LangGraph node: collect_news
    Collects articles from:
      1. Tier 1 Official scrapers (CBE, FRA)
      2. SearchManager (SerpAPI Google News & Competitors, with automatic RSS & Web Scraping fallback)
    Runs concurrently; persists new articles to DB. Returns deduplicated Article objects.
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
    force_fallback = state.get("force_search_fallback", False) or settings.force_search_fallback

    # 1. Prepare Tier 1 Official scrapers
    cbe_collector = CBECollector(timeout=settings.http_timeout_seconds)
    fra_collector = FRACollector(timeout=settings.http_timeout_seconds)

    # 2. Retrieve competitors and RSS sources from DB for search / fallback
    competitor_terms: list[str] = []
    competitor_objects: list[dict] = []
    db_rss_sources: list[dict] = []

    try:
        async with get_session() as session:
            comp_repo = CompetitorRepository(session)
            competitors = await comp_repo.get_active_competitors()
            for comp in competitors:
                competitor_terms.append(comp.name)
                for alias in (comp.aliases or []):
                    if alias and alias not in competitor_terms:
                        competitor_terms.append(alias)
                if comp.priority == 1:
                    competitor_objects.append({
                        "name": comp.name,
                        "aliases": comp.aliases or [],
                    })

            source_repo = SourceRepository(session)
            sources = await source_repo.get_active_sources()
            for src in sources:
                if src.source_type == "rss":
                    db_rss_sources.append({
                        "id": src.id,
                        "name": src.name,
                        "url": src.url,
                        "tier": src.tier,
                        "language": src.language,
                    })
    except Exception as exc:
        log.warning("node.collect_news.db_load_warning", error=str(exc))

    # 3. Instantiate SearchManager (with automated SerpAPI availability check & fallback)
    search_manager = SearchManager(
        timeout=settings.http_timeout_seconds,
        force_fallback=force_fallback,
    )

    # 4. Run official collectors and search manager concurrently
    tasks = [
        cbe_collector.safe_fetch(start_time, end_time),
        fra_collector.safe_fetch(start_time, end_time),
        search_manager.collect_news(
            start_time=start_time,
            end_time=end_time,
            competitor_names=competitor_terms,
            competitor_objects=competitor_objects,
            db_rss_sources=db_rss_sources,
        ),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    all_articles: list[Article] = []
    for batch in results:
        all_articles.extend(batch)

    log.info(
        "node.collect_news.raw_total",
        count=len(all_articles),
    )

    # Multi-level deduplication (URL, normalized title, content hash)
    unique_articles = deduplicate_articles(all_articles)

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
