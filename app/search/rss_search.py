"""
app/search/rss_search.py
────────────────────────
RSS-based fallback search collector.
Combines two robust, zero-cost retrieval streams:
  1. Free Google News Query RSS: Converts the same targeted queries (CBE, FRA,
     Consumer Finance, Competitors, Forsa) into Google News RSS feeds.
  2. Publisher RSS Feeds: Reads news directly from trusted Egyptian financial
     publishers (Amwal Al Ghad, Daily News Egypt, Al Borsa, Hapi, Economy Plus, etc.).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional, Sequence
from urllib.parse import quote_plus

import feedparser
import httpx
import structlog

from app.collectors.search import CATEGORY_QUERIES
from app.models.article import Article
from app.search.article_normalizer import clean_url, make_normalized_article, parse_datetime

log = structlog.get_logger(__name__)

GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"

# Curated fallback publisher feeds (if not already loaded from DB)
DEFAULT_PUBLISHER_FEEDS = [
    {"name": "Amwal Al Ghad", "url": "https://amwalalghad.com/feed/", "lang": "ar"},
    {"name": "Amwal Al Ghad Finance", "url": "https://amwalalghad.com/section/banks-finance/feed/", "lang": "ar"},
    {"name": "Daily News Egypt", "url": "https://dailynewsegypt.com/feed/", "lang": "en"},
    {"name": "Daily News Egypt Business", "url": "https://dailynewsegypt.com/category/business/feed/", "lang": "en"},
    {"name": "Al Borsa News", "url": "https://alborsaanews.com/feed/", "lang": "ar"},
    {"name": "Hapi Journal", "url": "https://hapijournal.com/feed/", "lang": "ar"},
    {"name": "Economy Plus", "url": "https://economyplusme.com/feed/", "lang": "ar"},
    {"name": "EnterpriseAM", "url": "https://enterpriseam.com/feed/", "lang": "en"},
    {"name": "Egypt Independent", "url": "https://www.egyptindependent.com/feed/", "lang": "en"},
    {"name": "ME Observer Finance", "url": "https://meobserver.news/business-economix/market-updates-finance/feed/", "lang": "en"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

_SEMAPHORE = asyncio.Semaphore(4)


class RSSSearchCollector:
    """Orchestrates query-based Google News RSS and publisher RSS feed ingestion."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    async def fetch_all(
        self,
        start_time: datetime,
        end_time: datetime,
        competitor_names: Optional[Sequence[str]] = None,
        db_rss_sources: Optional[Sequence[dict]] = None,
    ) -> list[Article]:
        """Fetch candidate articles from both query RSS feeds and publisher RSS feeds."""
        articles: list[Article] = []

        # 1. Query-based Google News RSS
        log.info("[FALLBACK] Fetching targeted Google News query RSS feeds...")
        query_articles = await self._fetch_google_news_queries(competitor_names)
        articles.extend(query_articles)
        log.info("[FALLBACK] Google News query RSS fetched.", count=len(query_articles))

        # 2. Publisher RSS feeds
        log.info("[FALLBACK] Fetching trusted publisher RSS feeds...")
        pub_articles = await self._fetch_publisher_feeds(db_rss_sources)
        articles.extend(pub_articles)
        log.info("[FALLBACK] Publisher RSS feeds fetched.", count=len(pub_articles))

        return articles

    async def _fetch_google_news_queries(
        self,
        competitor_names: Optional[Sequence[str]] = None,
    ) -> list[Article]:
        """Run category and competitor queries through Google News RSS."""
        queries: list[dict] = list(CATEGORY_QUERIES)

        # Add Forsa specific queries
        queries.extend([
            {"query": "Forsa consumer finance Egypt", "lang": "en"},
            {"query": "فرصة للتمويل الاستهلاكي مصر", "lang": "ar"},
            {"query": "شركة درايف للتمويل الاستهلاكي فرصة", "lang": "ar"},
        ])

        # Add competitor-specific queries
        if competitor_names:
            for cname in competitor_names[:12]:
                queries.append({
                    "query": f'"{cname}" (تمويل OR تقسيط OR BNPL OR finance)',
                    "lang": "ar",
                })

        articles: list[Article] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
            tasks = [self._fetch_single_query_rss(client, q_cfg) for q_cfg in queries]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, list):
                articles.extend(res)

        return articles

    async def _fetch_single_query_rss(
        self,
        client: httpx.AsyncClient,
        query_cfg: dict,
    ) -> list[Article]:
        """Fetch a single Google News RSS query."""
        query = query_cfg["query"]
        lang = query_cfg.get("lang", "en")
        gl = "EG"
        ceid = f"EG:{lang}"

        encoded_q = quote_plus(query)
        feed_url = f"{GOOGLE_NEWS_RSS_BASE}?q={encoded_q}&hl={lang}&gl={gl}&ceid={ceid}"

        async with _SEMAPHORE:
            try:
                resp = await client.get(feed_url, headers=HEADERS)
                if resp.status_code != 200:
                    return []
                feed = feedparser.parse(resp.content)
                items: list[Article] = []

                for entry in feed.entries:
                    title = getattr(entry, "title", "")
                    link = getattr(entry, "link", "")
                    summary = getattr(entry, "summary", "")
                    published = getattr(entry, "published_parsed", None) or getattr(entry, "published", None)

                    # Google News titles usually have ' - Source Name' at the end
                    source_name = "Google News"
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        if len(parts) == 2 and len(parts[1]) < 40:
                            source_name = parts[1].strip()

                    art = make_normalized_article(
                        title=title,
                        url=link,
                        source_name=source_name,
                        source_tier=2,
                        content=summary,
                        published_at=published,
                        language=lang,
                    )
                    if art:
                        items.append(art)
                return items
            except Exception as exc:
                log.debug("[FALLBACK] Query RSS fetch error", query=query[:40], error=str(exc))
                return []

    async def _fetch_publisher_feeds(
        self,
        db_rss_sources: Optional[Sequence[dict]] = None,
    ) -> list[Article]:
        """Fetch publisher RSS feeds (from DB or default list)."""
        feeds_to_fetch = list(db_rss_sources) if db_rss_sources else list(DEFAULT_PUBLISHER_FEEDS)

        articles: list[Article] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
            tasks = [self._fetch_single_publisher_feed(client, feed) for feed in feeds_to_fetch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, list):
                articles.extend(res)

        return articles

    async def _fetch_single_publisher_feed(
        self,
        client: httpx.AsyncClient,
        feed_info: dict,
    ) -> list[Article]:
        """Fetch and parse an individual publisher RSS feed."""
        feed_url = feed_info.get("url") or feed_info.get("feed_url")
        source_name = feed_info.get("name") or feed_info.get("source_name", "RSS Feed")
        tier = feed_info.get("tier", 2)
        lang = feed_info.get("language") or feed_info.get("lang", "en")
        source_id = feed_info.get("id")

        if not feed_url:
            return []

        async with _SEMAPHORE:
            try:
                resp = await client.get(feed_url, headers=HEADERS)
                if resp.status_code != 200:
                    return []
                feed = feedparser.parse(resp.content)
                items: list[Article] = []

                for entry in feed.entries:
                    title = getattr(entry, "title", "")
                    link = getattr(entry, "link", "")
                    summary = ""
                    if hasattr(entry, "content") and entry.content:
                        summary = entry.content[0].get("value", "")
                    elif hasattr(entry, "summary"):
                        summary = entry.summary or ""

                    published = getattr(entry, "published_parsed", None) or getattr(entry, "published", None)

                    art = make_normalized_article(
                        title=title,
                        url=link,
                        source_name=source_name,
                        source_tier=tier,
                        source_id=source_id,
                        content=summary,
                        published_at=published,
                        language=lang,
                    )
                    if art:
                        items.append(art)
                return items
            except Exception as exc:
                log.warning("[FALLBACK] Publisher feed failed", source=source_name, url=feed_url, error=str(exc))
                return []
