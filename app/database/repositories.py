"""
app/database/repositories.py
─────────────────────────────
Repository pattern — one class per major table.
Business logic should NEVER call raw SQL directly; use these classes instead.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Sequence

import structlog
from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Article,
    ArticleClassification,
    Competitor,
    Event,
    EventArticle,
    SentNews,
    Source,
    WorkflowRun,
)

log = structlog.get_logger(__name__)


# ── SourceRepository ──────────────────────────────────────────────────────────

class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_active_sources(self) -> Sequence[Source]:
        result = await self._s.execute(
            select(Source).where(Source.active == True).order_by(Source.tier, Source.priority)
        )
        return result.scalars().all()

    async def get_by_name(self, name: str) -> Optional[Source]:
        result = await self._s.execute(select(Source).where(Source.name == name))
        return result.scalar_one_or_none()

    async def upsert(self, source: Source) -> Source:
        existing = await self.get_by_name(str(source.name))
        if existing:
            existing.url = source.url
            existing.tier = source.tier
            existing.priority = source.priority
            existing.active = source.active
            return existing
        self._s.add(source)
        await self._s.flush()
        return source


# ── CompetitorRepository ──────────────────────────────────────────────────────

class CompetitorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_active_competitors(self) -> Sequence[Competitor]:
        result = await self._s.execute(
            select(Competitor)
            .where(Competitor.active == True)
            .order_by(Competitor.priority, Competitor.name)
        )
        return result.scalars().all()

    async def get_all_search_terms(self) -> list[str]:
        """Return all competitor names + aliases as a flat list for search queries."""
        competitors = await self.get_active_competitors()
        terms: list[str] = []
        for c in competitors:
            terms.append(str(c.name))
            if isinstance(c.aliases, list):
                terms.extend([str(a) for a in c.aliases])
        return list(set(terms))

    async def upsert(self, competitor: Competitor) -> Competitor:
        result = await self._s.execute(
            select(Competitor).where(Competitor.name == competitor.name)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.aliases = competitor.aliases
            existing.website = competitor.website
            existing.priority = competitor.priority
            if competitor.active is not None:
                existing.active = competitor.active
            return existing
        self._s.add(competitor)
        await self._s.flush()
        return competitor


# ── ArticleRepository ─────────────────────────────────────────────────────────

class ArticleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def exists_by_url(self, url: str) -> bool:
        result = await self._s.execute(
            select(Article.id).where(Article.url == url).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_id_by_url(self, url: str) -> Optional[uuid.UUID]:
        result = await self._s.execute(
            select(Article.id).where(Article.url == url).limit(1)
        )
    async def get_existing_url_map(self, urls: list[str]) -> dict[str, uuid.UUID]:
        """Batch lookup URLs to IDs in a single query."""
        if not urls:
            return {}
        result = await self._s.execute(
            select(Article.url, Article.id).where(Article.url.in_(urls))
        )
        return {row[0]: row[1] for row in result.all()}

    async def exists_by_hash(self, content_hash: str) -> bool:
        result = await self._s.execute(
            select(Article.id).where(Article.content_hash == content_hash).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def save(self, article: Article) -> Article:
        self._s.add(article)
        await self._s.flush()
        return article

    async def save_many(self, articles: list[Article]) -> list[Article]:
        saved = []
        for a in articles:
            # Skip if URL already stored (idempotent)
            if not await self.exists_by_url(str(a.url)):
                self._s.add(a)
                saved.append(a)
        await self._s.flush()
        return saved

    async def get_by_window(
        self,
        start: datetime,
        end: datetime,
    ) -> Sequence[Article]:
        """Return articles published within the given UTC time window."""
        result = await self._s.execute(
            select(Article).where(
                and_(
                    Article.published_at >= start,
                    Article.published_at <= end,
                )
            )
        )
        return result.scalars().all()


# ── ClassificationRepository ──────────────────────────────────────────────────

class ClassificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, classification: ArticleClassification) -> ArticleClassification:
        self._s.add(classification)
        await self._s.flush()
        return classification


# ── EventRepository ───────────────────────────────────────────────────────────

class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, event: Event) -> Event:
        self._s.add(event)
        await self._s.flush()
        return event

    async def link_article(self, event_id: uuid.UUID, article_id: uuid.UUID) -> None:
        ea = EventArticle(event_id=event_id, article_id=article_id)
        self._s.add(ea)
        await self._s.flush()

    async def get_by_id(self, event_id: uuid.UUID) -> Optional[Event]:
        result = await self._s.execute(
            select(Event).where(Event.id == event_id)
        )
        return result.scalar_one_or_none()


# ── SentNewsRepository ────────────────────────────────────────────────────────

class SentNewsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def already_sent(self, event_id: uuid.UUID) -> bool:
        """
        Check whether this event has already been emailed to the CEO.
        This is the database-backed idempotency guard.
        """
        result = await self._s.execute(
            select(SentNews.id).where(SentNews.event_id == event_id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def is_event_already_sent(
        self,
        event_id: Optional[uuid.UUID] = None,
        canonical_url: Optional[str] = None,
        canonical_title: Optional[str] = None,
        article_urls: Optional[list[str]] = None,
    ) -> bool:
        """
        Check whether this event or any of its constituent articles/titles
        have already been emailed in a prior run.
        """
        # 1. Direct event_id check
        if event_id:
            res = await self._s.execute(
                select(SentNews.id).where(SentNews.event_id == event_id).limit(1)
            )
            if res.scalar_one_or_none() is not None:
                return True

        # 2. Canonical URL check against past sent events
        if canonical_url:
            res = await self._s.execute(
                select(SentNews.id)
                .join(Event, SentNews.event_id == Event.id)
                .where(Event.canonical_url == canonical_url)
                .limit(1)
            )
            if res.scalar_one_or_none() is not None:
                return True

        # 3. Article URLs check: did any article in this event get sent in a prior event?
        if article_urls:
            res = await self._s.execute(
                select(SentNews.id)
                .join(EventArticle, SentNews.event_id == EventArticle.event_id)
                .join(Article, EventArticle.article_id == Article.id)
                .where(Article.url.in_(article_urls))
                .limit(1)
            )
            if res.scalar_one_or_none() is not None:
                return True

        # 4. Canonical title match against events sent in the last 48 hours
        if canonical_title:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
            res = await self._s.execute(
                select(SentNews.id)
                .join(Event, SentNews.event_id == Event.id)
                .where(
                    and_(
                        SentNews.sent_at >= cutoff,
                        func.lower(Event.canonical_title) == canonical_title.strip().lower(),
                    )
                )
                .limit(1)
            )
            if res.scalar_one_or_none() is not None:
                return True

        return False

    async def record_sent(
        self,
        event_id: uuid.UUID,
        recipient: str,
        run_id: uuid.UUID,
    ) -> SentNews:
        record = SentNews(
            event_id=event_id,
            recipient=recipient,
            run_id=run_id,
            sent_at=datetime.now(timezone.utc),
        )
        self._s.add(record)
        await self._s.flush()
        return record


# ── WorkflowRunRepository ─────────────────────────────────────────────────────

class WorkflowRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, run_id: Optional[uuid.UUID] = None) -> WorkflowRun:
        run = WorkflowRun(
            id=run_id or uuid.uuid4(),
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
        )
        self._s.add(run)
        await self._s.flush()
        return run

    async def complete(
        self,
        run_id: uuid.UUID,
        *,
        articles_collected: int = 0,
        articles_relevant: int = 0,
        articles_deduplicated: int = 0,
        articles_verified: int = 0,
        events_detected: int = 0,
        events_sent: int = 0,
        summaries_generated: int = 0,
        email_sent: bool = False,
        error_message: Optional[str] = None,
    ) -> None:
        status = "FAILED" if error_message and events_sent == 0 else (
            "PARTIAL" if error_message else "COMPLETED"
        )
        await self._s.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .values(
                completed_at=datetime.now(timezone.utc),
                status=status,
                articles_collected=articles_collected,
                articles_relevant=articles_relevant,
                articles_deduplicated=articles_deduplicated,
                articles_verified=articles_verified,
                events_detected=events_detected,
                events_sent=events_sent,
                summaries_generated=summaries_generated,
                email_sent=email_sent,
                error_message=error_message,
            )
        )

    async def get_last_successful_run_time(self) -> Optional[datetime]:
        """
        Return the started_at timestamp of the most recent COMPLETED run with events sent.
        Used to determine the collection window for the next run.
        """
        result = await self._s.execute(
            select(WorkflowRun.started_at)
            .where(WorkflowRun.status.in_(["COMPLETED", "PARTIAL"]))
            .where(WorkflowRun.events_sent > 0)
            .order_by(WorkflowRun.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
