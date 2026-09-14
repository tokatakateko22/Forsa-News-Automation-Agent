"""
app/collectors/search.py
─────────────────────────
SerpAPI Google News search collector.
Runs structured queries for each monitoring category and the competitor watchlist.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import structlog
from dateutil import parser as dparser

from app.collectors.base import NewsSourceCollector
from app.config import settings
from app.models.article import Article

log = structlog.get_logger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"

# ── Category search queries ───────────────────────────────────────────────────
# Each query targets a specific monitoring scope. Bilingual where useful.

CATEGORY_QUERIES: list[dict] = [
    # CBE / Monetary Policy
    {
        "query": "Central Bank of Egypt interest rate monetary policy",
        "category": "CBE",
        "lang": "en",
    },
    {
        "query": "البنك المركزي المصري سعر الفائدة السياسة النقدية",
        "category": "CBE",
        "lang": "ar",
    },
    {
        "query": "CBE MPC decision Egypt banking regulation",
        "category": "CBE",
        "lang": "en",
    },
    # FRA / Consumer Finance Regulation
    {
        "query": "Financial Regulatory Authority FRA Egypt consumer finance regulation",
        "category": "FRA",
        "lang": "en",
    },
    {
        "query": "الهيئة العامة للرقابة المالية تمويل استهلاكي قرار",
        "category": "FRA",
        "lang": "ar",
    },
    {
        "query": "FRA Egypt fintech BNPL consumer finance license",
        "category": "FRA",
        "lang": "en",
    },
    # Egyptian Consumer Finance Market
    {
        "query": "Egypt consumer finance BNPL installment lending market",
        "category": "Consumer Finance",
        "lang": "en",
    },
    {
        "query": "تمويل استهلاكي مصر اقساط تقسيط BNPL",
        "category": "Consumer Finance",
        "lang": "ar",
    },
    {
        "query": "Egypt digital lending fintech consumer credit",
        "category": "FinTech",
        "lang": "en",
    },
    # Egyptian Financial Market
    {
        "query": "Egypt banking fintech financial services market",
        "category": "Financial Market",
        "lang": "en",
    },
    {
        "query": "Egypt financial inclusion payments digital banking",
        "category": "Financial Market",
        "lang": "en",
    },
    # Egyptian Economy (scoped)
    {
        "query": "Egypt inflation GDP exchange rate fiscal policy economy",
        "category": "Economy",
        "lang": "en",
    },
    {
        "query": "مصر تضخم ناتج محلي سعر صرف اقتصاد",
        "category": "Economy",
        "lang": "ar",
    },
]


def _days_back(start_time: datetime, end_time: datetime) -> str:
    """Convert window to SerpAPI Google News 'tbs' parameter."""
    delta = end_time - start_time
    if delta.total_seconds() <= 86400 * 2:
        return "qdr:d"
    elif delta.total_seconds() <= 86400 * 7:
        return "qdr:w"
    return "qdr:m"


class SerpAPICollector(NewsSourceCollector):
    """
    Uses SerpAPI Google News to discover articles across all monitoring categories.
    One HTTP call per query; results are filtered by date window.
    """

    source_name = "SerpAPI Google News"
    source_tier = 2

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self._api_key = settings.serpapi_key

    async def fetch(self, start_time: datetime, end_time: datetime) -> list[Article]:
        articles: list[Article] = []
        tbs = _days_back(start_time, end_time)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for query_cfg in CATEGORY_QUERIES:
                try:
                    results = await self._search(client, query_cfg["query"], tbs)
                    for r in results:
                        pub_date = self._parse_date(r.get("date", ""), end_time)
                        if pub_date and pub_date < start_time:
                            continue
                        article = self._make_article(
                            title=r.get("title", "").strip(),
                            url=r.get("link", "").strip(),
                            content=r.get("snippet", ""),
                            published_at=pub_date,
                            language=query_cfg.get("lang", "en"),
                        )
                        if article.title and article.url:
                            articles.append(article)
                except Exception as exc:
                    log.warning(
                        "serpapi.query_failed",
                        query=query_cfg["query"][:60],
                        error=str(exc),
                    )

        log.info("serpapi.fetched", total=len(articles))
        return articles

    async def _search(
        self, client: httpx.AsyncClient, query: str, tbs: str
    ) -> list[dict]:
        params = {
            "engine": "google_news",
            "q": query,
            "tbs": tbs,
            "api_key": self._api_key,
            "num": 20,
            "hl": "en",
            "gl": "eg",
        }
        response = await client.get(SERPAPI_BASE, params=params)
        response.raise_for_status()
        data = response.json()
        # SerpAPI returns news_results for Google News engine
        return data.get("news_results", [])

    def _parse_date(self, raw: str, fallback: datetime) -> Optional[datetime]:
        """Parse SerpAPI date strings like '2 hours ago', '3 days ago', or ISO."""
        if not raw:
            return fallback
        try:
            return dparser.parse(raw, fuzzy=True).astimezone(timezone.utc)
        except Exception:
            pass
        # Try relative strings
        import re
        now = datetime.now(timezone.utc)
        m = re.search(r"(\d+)\s+(minute|hour|day|week)", raw, re.IGNORECASE)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            delta_map = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}
            return now - timedelta(seconds=n * delta_map.get(unit, 3600))
        return fallback


_COMPETITOR_SEMAPHORE = asyncio.Semaphore(2)


class SerpAPICompetitorCollector(NewsSourceCollector):
    """
    Runs targeted SerpAPI queries for each active competitor.
    Uses competitor name + all aliases as search terms.
    """

    source_name = "SerpAPI Competitor Monitor"
    source_tier = 2

    def __init__(
        self,
        competitor_name: str,
        aliases: list[str],
        timeout: int = 30,
    ) -> None:
        self.competitor_name = competitor_name
        self.aliases = aliases
        self.timeout = timeout
        self._api_key = settings.serpapi_key

    async def fetch(self, start_time: datetime, end_time: datetime) -> list[Article]:
        articles: list[Article] = []
        tbs = _days_back(start_time, end_time)

        # Build a targeted query: primary name + up to 3 aliases
        terms = [self.competitor_name] + self.aliases[:3]
        query = f'("{self.competitor_name}" OR "{self.aliases[0] if self.aliases else self.competitor_name}") Egypt finance'

        async with _COMPETITOR_SEMAPHORE:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                try:
                    params = {
                        "engine": "google_news",
                        "q": query,
                        "tbs": tbs,
                        "api_key": self._api_key,
                        "num": 15,
                        "gl": "eg",
                    }
                    response = await client.get(SERPAPI_BASE, params=params)
                    response.raise_for_status()
                    data = response.json()
                    results = data.get("news_results", [])

                    for r in results:
                        pub_date = self._parse_date(r.get("date", ""), end_time)
                        if pub_date and pub_date < start_time:
                            continue
                        article = self._make_article(
                            title=r.get("title", "").strip(),
                            url=r.get("link", "").strip(),
                            content=r.get("snippet", ""),
                            published_at=pub_date,
                        )
                        if article.title and article.url:
                            articles.append(article)
                except Exception as exc:
                    log.warning(
                        "serpapi.competitor_failed",
                        competitor=self.competitor_name,
                        error=str(exc),
                    )

        return articles

    def _parse_date(self, raw: str, fallback: datetime) -> Optional[datetime]:
        if not raw:
            return fallback
        try:
            return dparser.parse(raw, fuzzy=True).astimezone(timezone.utc)
        except Exception:
            pass
        import re
        now = datetime.now(timezone.utc)
        m = re.search(r"(\d+)\s+(minute|hour|day|week)", raw, re.IGNORECASE)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            delta_map = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}
            return now - timedelta(seconds=n * delta_map.get(unit, 3600))
        return fallback
