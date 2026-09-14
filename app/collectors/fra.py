"""
app/collectors/fra.py
──────────────────────
Collector for the Financial Regulatory Authority (FRA) official website.
Scrapes FRA news, decisions, circulars, and licensing announcements.
Tier 1 — highest priority source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup
from dateutil import parser as dparser

from app.collectors.base import NewsSourceCollector
from app.models.article import Article

log = structlog.get_logger(__name__)

FRA_PAGES = [
    {
        "url": "https://fra.gov.eg/en/fra_news/",
        "language": "en",
        "base": "https://fra.gov.eg",
    },
    {
        "url": "https://fra.gov.eg/fra_news/",
        "language": "ar",
        "base": "https://fra.gov.eg",
    },
    {
        "url": "https://fra.gov.eg/en/decisions/",
        "language": "en",
        "base": "https://fra.gov.eg",
    },
    {
        "url": "https://fra.gov.eg/decisions/",
        "language": "ar",
        "base": "https://fra.gov.eg",
    },
]

HEADERS = {
    "User-Agent": "ForsaNewsAgent/1.0 (+https://forsaegypt.com)",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


class FRACollector(NewsSourceCollector):
    """
    Scrapes FRA official news, regulatory decisions, and licensing updates.
    Returns Tier 1 articles.
    """

    source_name = "Financial Regulatory Authority"
    source_tier = 1

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    async def fetch(self, start_time: datetime, end_time: datetime) -> list[Article]:
        articles: list[Article] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for page_cfg in FRA_PAGES:
                try:
                    items = await self._scrape_page(client, page_cfg, start_time, end_time)
                    articles.extend(items)
                except Exception as exc:
                    log.warning(
                        "fra.page_failed",
                        url=page_cfg["url"],
                        error=str(exc),
                    )
        # Deduplicate by URL
        seen: set[str] = set()
        unique = []
        for a in articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)
        return unique

    async def _scrape_page(
        self,
        client: httpx.AsyncClient,
        cfg: dict,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Article]:
        response = await client.get(cfg["url"], headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        articles: list[Article] = []

        # FRA WordPress-based site — look for article/post elements
        selectors = [
            "article.post",
            ".post-item",
            ".news-post",
            "article",
            ".entry-title a",
        ]
        items = []
        for sel in selectors:
            items = soup.select(sel)
            if items:
                break

        for item in items:
            link_tag = item if item.name == "a" else item.find("a", href=True)
            if not link_tag:
                continue

            title = link_tag.get_text(strip=True)
            href = link_tag.get("href", "")
            if not href or not title or len(title) < 10:
                continue

            url = urljoin(cfg["base"], href)
            pub_date = self._extract_date(item)

            if pub_date and (pub_date < start_time or pub_date > end_time):
                continue

            articles.append(
                self._make_article(
                    title=title,
                    url=url,
                    published_at=pub_date,
                    language=cfg["language"],
                )
            )

        return articles

    def _extract_date(self, element) -> Optional[datetime]:
        """Extract date from FRA element."""
        time_tag = element.find("time")
        if time_tag:
            dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
            try:
                return dparser.parse(dt_str, fuzzy=True).astimezone(timezone.utc)
            except Exception:
                pass

        # date class patterns common in WordPress themes
        for cls in ["date", "entry-date", "published", "post-date"]:
            date_el = element.find(class_=cls)
            if date_el:
                try:
                    return dparser.parse(
                        date_el.get_text(strip=True), fuzzy=True
                    ).astimezone(timezone.utc)
                except Exception:
                    pass
        return None
