"""
app/search/website_search.py
────────────────────────────
Direct website scraper for trusted Egyptian financial news sources that lack
stable RSS feeds (or to supplement RSS coverage when SerpAPI is exhausted).
Supported target sites:
  1. Enterprise Egypt (https://enterprise.press/)
  2. Zawya Egypt / North Africa Financials (https://www.zawya.com/en/economy/north-africa/)
  3. Al Mal News - Banks & Finance (https://almalnews.com/category/banks/)
  4. Daily News Egypt - Business (https://dailynewsegypt.com/category/business/)
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup

from app.models.article import Article
from app.search.article_normalizer import make_normalized_article

log = structlog.get_logger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

WEBSITE_TARGETS = [
    {
        "name": "Enterprise Egypt",
        "url": "https://enterprise.press/",
        "lang": "en",
        "tier": 2,
    },
    {
        "name": "Zawya North Africa",
        "url": "https://www.zawya.com/en/economy/north-africa/",
        "lang": "en",
        "tier": 2,
    },
    {
        "name": "Al Mal News",
        "url": "https://almalnews.com/category/banks/",
        "lang": "ar",
        "tier": 2,
    },
    {
        "name": "Daily News Egypt",
        "url": "https://dailynewsegypt.com/category/business/",
        "lang": "en",
        "tier": 2,
    },
]


class DirectWebsiteCollector:
    """Scrapes financial news portals directly using BeautifulSoup."""

    def __init__(self, timeout: float = 25.0) -> None:
        self.timeout = timeout

    async def fetch_all(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Article]:
        """Scrape all configured website targets in parallel."""
        log.info("[FALLBACK] Starting direct website scraping for trusted financial sources...")
        articles: list[Article] = []

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
            tasks = [self._scrape_site(client, target) for target in WEBSITE_TARGETS]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, list):
                articles.extend(res)

        log.info("[FALLBACK] Direct website scraping finished.", total_articles=len(articles))
        return articles

    async def _scrape_site(self, client: httpx.AsyncClient, target: dict) -> list[Article]:
        site_name = target["name"]
        site_url = target["url"]
        lang = target["lang"]
        tier = target["tier"]

        try:
            resp = await client.get(site_url, headers=HEADERS)
            if resp.status_code != 200:
                log.warning("[FALLBACK] Site returned non-200", site=site_name, status=resp.status_code)
                return []

            soup = BeautifulSoup(resp.content, "html.parser")
            items: list[Article] = []

            # Extract article links and titles based on standard HTML conventions
            candidates = soup.find_all(["article", "div", "li"], class_=lambda c: c and any(
                w in str(c).lower() for w in ("post", "article", "news-item", "story", "card", "entry")
            ))

            if not candidates:
                # Fallback: look for headline tags
                candidates = soup.find_all(["h2", "h3", "h4"])

            for element in candidates[:30]:
                link_tag = element.find("a") if element.name != "a" else element
                if not link_tag or not link_tag.get("href"):
                    continue

                href = link_tag.get("href", "").strip()
                title = link_tag.get_text(strip=True)
                if not title or len(title) < 12:
                    continue

                full_url = urljoin(site_url, href)

                # Find summary/paragraph if available
                snippet = ""
                p_tag = element.find("p")
                if p_tag:
                    snippet = p_tag.get_text(strip=True)

                art = make_normalized_article(
                    title=title,
                    url=full_url,
                    source_name=site_name,
                    source_tier=tier,
                    content=snippet,
                    published_at=datetime.now(),  # Fallback to current window
                    language=lang,
                )
                if art:
                    items.append(art)

            log.info("[FALLBACK] Site scraped successfully", site=site_name, count=len(items))
            return items

        except Exception as exc:
            log.warning("[FALLBACK] Failed to scrape site", site=site_name, url=site_url, error=str(exc))
            return []
