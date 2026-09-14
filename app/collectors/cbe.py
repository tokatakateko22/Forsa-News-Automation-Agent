"""
app/collectors/cbe.py
──────────────────────
Collector for the Central Bank of Egypt (CBE) official website.
Scrapes the CBE news/press-release sections in both Arabic and English.
Tier 1 — highest priority source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup

from app.collectors.base import NewsSourceCollector
from app.models.article import Article

log = structlog.get_logger(__name__)

CBE_PAGES = [
    {
        "url": "https://www.cbe.org.eg/en/monetary-policy/mpc-press-releases",
        "language": "en",
        "base": "https://www.cbe.org.eg",
    },
    {
        "url": "https://www.cbe.org.eg/en/press-releases",
        "language": "en",
        "base": "https://www.cbe.org.eg",
    },
    {
        "url": "https://www.cbe.org.eg/ar/monetary-policy/mpc-press-releases",
        "language": "ar",
        "base": "https://www.cbe.org.eg",
    },
    {
        "url": "https://www.cbe.org.eg/ar/press-releases",
        "language": "ar",
        "base": "https://www.cbe.org.eg",
    },
]

HEADERS = {
    "User-Agent": "ForsaNewsAgent/1.0 (+https://forsaegypt.com)",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


class CBECollector(NewsSourceCollector):
    """
    Scrapes CBE official press-release and MPC-decision pages.
    Returns Tier 1 articles — highest importance weight.
    """

    source_name = "Central Bank of Egypt"
    source_tier = 1

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    async def fetch(self, start_time: datetime, end_time: datetime) -> list[Article]:
        articles: list[Article] = []
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for page_cfg in CBE_PAGES:
                try:
                    items = await self._scrape_page(client, page_cfg, start_time, end_time)
                    articles.extend(items)
                except Exception as exc:
                    log.warning(
                        "cbe.page_failed",
                        url=page_cfg["url"],
                        error=str(exc),
                    )
        # Deduplicate by URL within this source
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

        # CBE uses various list/card patterns — try multiple selectors
        selectors = [
            "article",
            ".press-release-item",
            ".news-item",
            ".list-item",
            "li.item",
        ]
        items = []
        for sel in selectors:
            items = soup.select(sel)
            if items:
                break

        # Fallback: grab all <a> tags with date context
        if not items:
            items = soup.select("a[href]")

        for item in items:
            link_tag = item if item.name == "a" else item.find("a", href=True)
            if not link_tag:
                continue

            title = link_tag.get_text(strip=True)
            href = link_tag.get("href", "")
            if not href or not title or len(title) < 10:
                continue

            url = urljoin(cfg["base"], href)

            # Try to find a date
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

    def _extract_date(self, element: BeautifulSoup) -> Optional[datetime]:
        """Try to find a publication date in or near the element."""
        import re
        from dateutil import parser as dparser

        # Look for time tags or date-bearing attributes
        time_tag = element.find("time")
        if time_tag:
            dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
            try:
                return dparser.parse(dt_str, fuzzy=True).astimezone(timezone.utc)
            except Exception:
                pass

        # Look for date-pattern text in the element
        text = element.get_text(" ", strip=True)
        date_patterns = [
            r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
            r"\d{4}[/-]\d{1,2}[/-]\d{1,2}",
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}",
        ]
        for pat in date_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    return dparser.parse(m.group(), fuzzy=True).astimezone(timezone.utc)
                except Exception:
                    pass
        return None
