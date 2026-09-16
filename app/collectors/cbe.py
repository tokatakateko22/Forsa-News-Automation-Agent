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
        "url": "https://www.cbe.org.eg/en/news-publications/news",
        "language": "en",
        "base": "https://www.cbe.org.eg",
    },
    {
        "url": "https://www.cbe.org.eg/ar/news-publications/news",
        "language": "ar",
        "base": "https://www.cbe.org.eg",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
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
        log.info("cbe.fetched", total=len(unique))
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

        # Find all links with meaningful text
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(" ", strip=True)

            # Filter for news/press release URLs with year paths
            if "/news/202" not in href and "/mpc-press-releases/" not in href:
                continue
            if not title or len(title) < 15:
                continue

            url = urljoin(cfg["base"], href)
            pub_date = self._extract_date(a_tag, href)

            if pub_date and (pub_date < start_time or pub_date > end_time):
                continue

            # Fetch article page to extract full body content
            content = ""
            try:
                art_resp = await client.get(url, headers=HEADERS)
                if art_resp.status_code == 200:
                    art_soup = BeautifulSoup(art_resp.text, "lxml")
                    for tag in art_soup(["script", "style", "nav", "header", "footer"]):
                        tag.decompose()
                    paragraphs = [
                        p.get_text(" ", strip=True)
                        for p in art_soup.find_all("p")
                        if len(p.get_text(" ", strip=True)) > 20
                    ]
                    content = "\n\n".join(paragraphs)
            except Exception as exc:
                log.warning("cbe.article_fetch_failed", url=url, error=str(exc))

            articles.append(
                self._make_article(
                    title=title,
                    url=url,
                    content=content or None,
                    published_at=pub_date or end_time,
                    language=cfg["language"],
                )
            )

        return articles

    def _extract_date(self, element: BeautifulSoup, href: str = "") -> Optional[datetime]:
        """Extract date from URL path (/YYYY/MM/DD/) or element text/time tags."""
        import re
        from dateutil import parser as dparser

        # 1. First priority: Check date embedded directly in CBE URL (e.g. /2026/09/07/)
        if href:
            m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", href)
            if m:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try:
                    return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
                except ValueError:
                    pass

        # 2. Check time tag
        time_tag = element.find("time")
        if time_tag:
            dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
            try:
                return dparser.parse(dt_str, fuzzy=True).astimezone(timezone.utc)
            except Exception:
                pass

        # 3. Look for date-pattern text in parent/context
        parent = element.parent
        text = (parent.get_text(" ", strip=True) if parent else "") + " " + element.get_text(" ", strip=True)
        date_patterns = [
            r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
            r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b",
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
