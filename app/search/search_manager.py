"""
app/search/search_manager.py
────────────────────────────
Unified Search Manager orchestrating primary SerpAPI search and automatic fallback retrieval.
Implements the core decision tree:
    search_news()
        │
        ├── Check SerpApi availability (pre-flight)
        │
        ├── if available:
        │       └── Run SerpApi Google News + Competitor search
        │           (If in-flight 429/quota error occurs → switches to fallback)
        │
        └── if unavailable / quota exhausted:
                ├── Fetch query-based Google News RSS (Free)
                ├── Fetch publisher RSS feeds (Amwal Al Ghad, DNE, Al Borsa, etc.)
                ├── Scrape direct financial website sections
                ├── Normalize articles
                ├── Deduplicate articles
                ├── Filter by date window & topic relevance
                └── Return normalized articles
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, Sequence

import structlog

from app.collectors.search import SerpAPICollector, SerpAPICompetitorCollector
from app.config import settings
from app.models.article import Article
from app.search.article_normalizer import deduplicate_articles
from app.search.relevance_filter import filter_fallback_articles
from app.search.rss_search import RSSSearchCollector
from app.search.serpapi_checker import SerpApiStatus, check_serpapi_availability
from app.search.website_search import DirectWebsiteCollector

log = structlog.get_logger(__name__)


class SearchManager:
    """Central orchestrator for news discovery with automated fallback resilience."""

    def __init__(
        self,
        timeout: Optional[float] = None,
        force_fallback: Optional[bool] = None,
    ) -> None:
        self.timeout = timeout or settings.http_timeout_seconds
        self.force_fallback = force_fallback
        self.rss_collector = RSSSearchCollector(timeout=self.timeout)
        self.website_collector = DirectWebsiteCollector(timeout=self.timeout)

    async def collect_news(
        self,
        start_time: datetime,
        end_time: datetime,
        competitor_names: Optional[Sequence[str]] = None,
        competitor_objects: Optional[Sequence[dict]] = None,
        db_rss_sources: Optional[Sequence[dict]] = None,
    ) -> list[Article]:
        """
        Execute search: uses SerpAPI if quota is available, otherwise activates fallback.
        """
        # Step 1: Pre-flight check
        status: SerpApiStatus = await check_serpapi_availability(
            timeout=8.0,
            force_fallback=self.force_fallback,
        )

        if status.is_available:
            log.info(
                f"[SEARCH] SerpApi quota available ({status.searches_remaining} searches remaining). Using SerpApi."
            )
            try:
                articles = await self._run_serpapi(
                    start_time=start_time,
                    end_time=end_time,
                    competitor_objects=competitor_objects,
                )
                if articles:
                    return articles
                log.info("[SEARCH] SerpApi returned 0 articles. Attempting fallback retrieval as supplement.")
            except Exception as exc:
                err_str = str(exc).lower()
                if "429" in err_str or "run out of searches" in err_str or "quota" in err_str:
                    log.warning(
                        "[SEARCH] SerpApi quota exhausted during run! Activating fallback retrieval...",
                        error=str(exc),
                    )
                else:
                    log.warning(
                        "[SEARCH] SerpApi encountered unexpected error. Activating fallback retrieval...",
                        error=str(exc),
                    )

        # Step 2: SerpAPI is unavailable or exhausted -> Activate fallback retrieval
        log.warning(
            f"[SEARCH] SerpApi unavailable or quota exhausted (Reason: {status.reason})."
        )
        log.info("[SEARCH] Activating fallback retrieval system...")

        return await self._run_fallback(
            start_time=start_time,
            end_time=end_time,
            competitor_names=competitor_names,
            db_rss_sources=db_rss_sources,
        )

    async def _run_serpapi(
        self,
        start_time: datetime,
        end_time: datetime,
        competitor_objects: Optional[Sequence[dict]] = None,
    ) -> list[Article]:
        """Execute primary search via SerpAPI."""
        serp_collector = SerpAPICollector(timeout=int(self.timeout))
        tasks = [serp_collector.safe_fetch(start_time, end_time)]

        # Run competitor collectors
        if competitor_objects:
            for comp in competitor_objects:
                c_name = comp.get("name")
                aliases = comp.get("aliases") or []
                if c_name:
                    c_collector = SerpAPICompetitorCollector(
                        competitor_name=c_name,
                        aliases=aliases,
                        timeout=int(self.timeout),
                    )
                    tasks.append(c_collector.safe_fetch(start_time, end_time))

        results = await asyncio.gather(*tasks, return_exceptions=False)
        articles: list[Article] = []
        for batch in results:
            articles.extend(batch)

        log.info("[SEARCH] SerpApi search completed.", total_articles=len(articles))
        return articles

    async def _run_fallback(
        self,
        start_time: datetime,
        end_time: datetime,
        competitor_names: Optional[Sequence[str]] = None,
        db_rss_sources: Optional[Sequence[dict]] = None,
    ) -> list[Article]:
        """Execute fallback retrieval using query RSS, publisher RSS, and website scrapers."""
        log.info("[FALLBACK] Starting multi-stream fallback retrieval...")

        tasks = [
            self.rss_collector.fetch_all(
                start_time=start_time,
                end_time=end_time,
                competitor_names=competitor_names,
                db_rss_sources=db_rss_sources,
            ),
            self.website_collector.fetch_all(
                start_time=start_time,
                end_time=end_time,
            ),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        raw_candidates: list[Article] = []
        for res in results:
            if isinstance(res, list):
                raw_candidates.extend(res)
            elif isinstance(res, Exception):
                log.error("[FALLBACK] Subsystem error during fallback retrieval", error=str(res))

        log.info(f"[FALLBACK] Retrieved {len(raw_candidates)} candidate articles across fallback sources.")

        if not raw_candidates:
            log.warning("[FALLBACK] No candidate articles discovered across fallback sources.")
            return []

        # Deduplication
        unique_candidates = deduplicate_articles(raw_candidates)
        dup_count = len(raw_candidates) - len(unique_candidates)
        log.info(f"[FILTER] Deduplication: {dup_count} duplicate articles removed ({len(unique_candidates)} unique).")

        # Date & Relevance Filtering
        filtered = filter_fallback_articles(
            articles=unique_candidates,
            start_time=start_time,
            end_time=end_time,
            competitor_names=competitor_names,
        )

        log.info(f"[RESULT] {len(filtered)} relevant fallback articles selected for downstream pipeline.")
        return filtered
