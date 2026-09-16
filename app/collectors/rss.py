"""
app/collectors/rss.py
──────────────────────
Generic RSS/Atom feed collector.
Reads active RSS-type sources from the DB and fetches new articles.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
import structlog

from app.collectors.base import NewsSourceCollector
from app.models.article import Article

log = structlog.get_logger(__name__)


def _parse_date(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """Extract published date from a feedparser entry."""
    import email.utils
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                import time
                ts = time.mktime(val)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
    # Try raw string parsing
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                parsed = email.utils.parsedate_to_datetime(raw)
                return parsed.astimezone(timezone.utc)
            except Exception:
                pass
    return None


def _entry_content(entry: feedparser.FeedParserDict) -> str:
    """Extract the best available text content from a feedparser entry."""
    if hasattr(entry, "content") and entry.content:
        return entry.content[0].get("value", "")
    if hasattr(entry, "summary"):
        return entry.summary or ""
    return ""


class RSSCollector(NewsSourceCollector):
    """
    Collects articles from a single RSS/Atom feed URL.
    Filters by publication date window.
    """

    def __init__(
        self,
        feed_url: str,
        source_name: str,
        source_tier: int = 2,
        source_id: Optional[int] = None,
        language: str = "en",
        timeout: int = 30,
    ) -> None:
        self.feed_url = feed_url
        self.source_name = source_name
        self.source_tier = source_tier
        self.source_id = source_id
        self.language = language
        self.timeout = timeout

    async def fetch(self, start_time: datetime, end_time: datetime) -> list[Article]:
        log.info("rss.fetching", url=self.feed_url, source=self.source_name)

        # Download feed content asynchronously
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, verify=False) as client:
            response = await client.get(self.feed_url, headers=headers)
            response.raise_for_status()
            raw_content = response.content

        feed = feedparser.parse(raw_content)
        articles: list[Article] = []

        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            url = getattr(entry, "link", "").strip()

            if not title or not url:
                continue

            pub_date = _parse_date(entry)

            # Filter by time window if we have a date
            if pub_date is not None:
                if pub_date < start_time or pub_date > end_time:
                    continue

            content = _entry_content(entry)

            article = self._make_article(
                title=title,
                url=url,
                content=content,
                published_at=pub_date,
                language=self.language,
                source_id=self.source_id,
            )
            articles.append(article)

        log.info(
            "rss.fetched",
            source=self.source_name,
            total_entries=len(feed.entries),
            in_window=len(articles),
        )
        return articles
