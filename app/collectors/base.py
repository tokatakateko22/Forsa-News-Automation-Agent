"""
app/collectors/base.py
───────────────────────
Abstract base class for all news source collectors.
Every collector implements fetch(start_time, end_time) -> List[Article].
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import structlog

from app.models.article import Article

log = structlog.get_logger(__name__)


class NewsSourceCollector(ABC):
    """
    Abstract base for all news collectors.
    Implementations must be fault-tolerant: exceptions should be caught
    internally and logged; fetch() should return an empty list on failure,
    not raise an exception that crashes the pipeline.
    """

    source_name: str = "Unknown"
    source_tier: int = 2

    @abstractmethod
    async def fetch(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Article]:
        """
        Fetch articles published between start_time and end_time (UTC).
        Must never raise — return [] on failure.
        """
        ...

    async def safe_fetch(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Article]:
        """
        Wrapper that ensures exceptions are isolated per source.
        The pipeline calls this, not fetch() directly.
        """
        try:
            articles = await self.fetch(start_time, end_time)
            log.info(
                "collector.success",
                source=self.source_name,
                count=len(articles),
            )
            return articles
        except Exception as exc:
            log.error(
                "collector.failed",
                source=self.source_name,
                error=str(exc),
                exc_info=True,
            )
            return []

    def _make_article(
        self,
        *,
        title: str,
        url: str,
        content: Optional[str] = None,
        published_at: Optional[datetime] = None,
        language: str = "en",
        source_id: Optional[int] = None,
    ) -> Article:
        """Helper to construct a normalised Article from collector-specific data."""
        return Article(
            title=title.strip(),
            url=url.strip(),
            source_name=self.source_name,
            source_tier=self.source_tier,
            source_id=source_id,
            content=content,
            published_at=published_at,
            language=language,
        )
