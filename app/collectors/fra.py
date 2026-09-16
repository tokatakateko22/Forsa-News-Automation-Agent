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
        "url": "https://fra.gov.eg/",
        "language": "ar",
        "base": "https://fra.gov.eg",
    },
    {
        "url": "https://fra.gov.eg/category/mc/newsevents/releases/",
        "language": "ar",
        "base": "https://fra.gov.eg",
    },
    {
        "url": "https://fra.gov.eg/en/",
        "language": "en",
        "base": "https://fra.gov.eg",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

AR_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4,
    "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
    "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
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
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
            for page_cfg in FRA_PAGES:
                try:
                    items = await self._scrape_page(client, page_cfg, start_time, end_time, seen_urls)
                    articles.extend(items)
                except Exception as exc:
                    log.warning(
                        "fra.page_failed",
                        url=page_cfg["url"],
                        error=str(exc),
                    )

        log.info("fra.fetched", total=len(articles))
        return articles

    async def _scrape_page(
        self,
        client: httpx.AsyncClient,
        cfg: dict,
        start_time: datetime,
        end_time: datetime,
        seen_urls: set[str],
    ) -> list[Article]:
        response = await client.get(cfg["url"], headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        articles: list[Article] = []

        candidates: list[tuple[str, str, BeautifulSoup]] = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(" ", strip=True)

            if "/fra_news/" not in href or not title or len(title) < 15:
                continue

            full_url = urljoin(cfg["base"], href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            candidates.append((title, full_url, a_tag))

        for title, full_url, a_tag in candidates:
            # 1. Try to extract date from parent card
            parent = a_tag.find_parent(["div", "article", "li"])
            p_text = parent.get_text(" ", strip=True) if parent else ""
            pub_date = self._parse_date(p_text)

            # 2. Fetch article page to extract full content and date if needed
            content = ""
            try:
                art_resp = await client.get(full_url, headers=HEADERS)
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
                    if not pub_date:
                        pub_date = self._parse_date(content[:1000])
            except Exception as exc:
                log.warning("fra.article_fetch_failed", url=full_url, error=str(exc))

            # If date is outside collection window, skip
            if pub_date and (pub_date < start_time or pub_date > end_time):
                continue

            articles.append(
                self._make_article(
                    title=title,
                    url=full_url,
                    content=content or None,
                    published_at=pub_date or end_time,
                    language=cfg["language"],
                )
            )

        return articles

    def _parse_date(self, text: str) -> Optional[datetime]:
        """Extract date from text using Arabic or English date regex."""
        import re
        if not text:
            return None

        # 1. Arabic format: 12 سبتمبر 2026
        m = re.search(
            r"(\d{1,2})\s+(يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر)\s+(\d{4})",
            text,
        )
        if m:
            try:
                day = int(m.group(1))
                mon = AR_MONTHS[m.group(2)]
                yr = int(m.group(3))
                return datetime(yr, mon, day, 12, 0, 0, tzinfo=timezone.utc)
            except Exception:
                pass

        # 2. English / numeric patterns
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
