"""
app/database/models.py
──────────────────────
SQLAlchemy ORM table definitions for the Forsa News Agent.
All timestamps are stored in UTC.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ── sources ───────────────────────────────────────────────────────────────────

class Source(Base):
    """
    Configurable news source registry.
    Sources can be added/disabled without code changes.
    """
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    url = Column(String(500), nullable=False)
    source_type = Column(
        String(50), nullable=False,
        comment="rss | scrape | api | official"
    )
    tier = Column(Integer, nullable=False, default=2, comment="1=official, 2=reputable, 3=other")
    priority = Column(Integer, nullable=False, default=50)
    active = Column(Boolean, nullable=False, default=True)
    language = Column(String(10), nullable=False, default="en")
    category_hint = Column(String(100), nullable=True, comment="CBE | FRA | General | etc.")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    articles = relationship("Article", back_populates="source_ref", lazy="raise")


# ── competitors ───────────────────────────────────────────────────────────────

class Competitor(Base):
    """
    Configurable competitor watchlist.
    Each competitor can have multiple aliases used in search queries.
    """
    __tablename__ = "competitors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    aliases = Column(JSON, nullable=False, default=list,
                     comment="List of name variants / search terms")
    website = Column(String(500), nullable=True)
    priority = Column(Integer, nullable=False, default=2, comment="1=direct, 2=adjacent, 3=market_intel")
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# ── articles ──────────────────────────────────────────────────────────────────

class Article(Base):
    """
    Raw article records collected from all sources.
    content_hash enables fast exact-duplicate detection.
    """
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url", name="uq_articles_url"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(Text, nullable=False)
    url = Column(String(2048), nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    source_name = Column(String(200), nullable=True, comment="Denormalized for sources not in sources table")
    published_at = Column(DateTime(timezone=True), nullable=True)
    retrieved_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    content = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True, index=True,
                          comment="SHA-256 of normalized content for exact dedup")
    language = Column(String(10), nullable=False, default="en")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    source_ref = relationship("Source", back_populates="articles", lazy="raise")
    classification = relationship(
        "ArticleClassification", back_populates="article", uselist=False, lazy="raise"
    )


# ── article_classifications ───────────────────────────────────────────────────

class ArticleClassification(Base):
    """
    LLM-assigned classification for each article.
    Internal use only — scores are NEVER sent to the CEO.
    """
    __tablename__ = "article_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(UUID(as_uuid=True), ForeignKey("articles.id"), nullable=False, unique=True)
    category = Column(String(100), nullable=True)
    subcategory = Column(String(200), nullable=True)
    entities = Column(JSON, nullable=True, comment="List of named entities detected")
    is_relevant = Column(Boolean, nullable=False, default=False)
    importance_score = Column(Integer, nullable=True, comment="0-100, internal only")
    relevance_score = Column(Integer, nullable=True, comment="0-100, internal only")
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    article = relationship("Article", back_populates="classification", lazy="raise")


# ── events ────────────────────────────────────────────────────────────────────

class Event(Base):
    """
    Deduplicated news event — one event may be covered by multiple articles.
    The CEO receives one email item per event.
    """
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_title = Column(Text, nullable=False)
    event_date = Column(DateTime(timezone=True), nullable=True)
    category = Column(String(100), nullable=True)
    subcategory = Column(String(200), nullable=True)
    canonical_url = Column(String(2048), nullable=True)
    canonical_source_name = Column(String(200), nullable=True)
    summary = Column(Text, nullable=True, comment="LLM-generated factual summary")
    importance_score = Column(Integer, nullable=True, comment="Internal only — never emailed")
    verification_status = Column(
        String(50), nullable=False, default="UNVERIFIED",
        comment="VERIFIED | UNVERIFIED | OFFICIAL"
    )
    verification_source = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    articles = relationship("EventArticle", back_populates="event", lazy="raise")
    sent_records = relationship("SentNews", back_populates="event", lazy="raise")


# ── event_articles ────────────────────────────────────────────────────────────

class EventArticle(Base):
    """Join table: links events to their constituent articles."""
    __tablename__ = "event_articles"

    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), primary_key=True)
    article_id = Column(UUID(as_uuid=True), ForeignKey("articles.id"), primary_key=True)

    event = relationship("Event", back_populates="articles", lazy="raise")
    article = relationship("Article", lazy="raise")


# ── sent_news ─────────────────────────────────────────────────────────────────

class SentNews(Base):
    """
    Permanent record of every event that has been emailed to the CEO.
    Prevents the same event from being sent more than once.
    """
    __tablename__ = "sent_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id"), nullable=False, unique=True)
    sent_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    recipient = Column(String(500), nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.id"), nullable=True)

    event = relationship("Event", back_populates="sent_records", lazy="raise")
    run = relationship("WorkflowRun", back_populates="sent_records", lazy="raise")


# ── workflow_runs ─────────────────────────────────────────────────────────────

class WorkflowRun(Base):
    """
    Execution log for every pipeline run.
    Also stores the timestamp of the last successful run for windowed retrieval.
    """
    __tablename__ = "workflow_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        String(50), nullable=False, default="RUNNING",
        comment="RUNNING | COMPLETED | FAILED | PARTIAL"
    )
    articles_collected = Column(Integer, nullable=False, default=0)
    articles_relevant = Column(Integer, nullable=False, default=0)
    articles_deduplicated = Column(Integer, nullable=False, default=0)
    articles_verified = Column(Integer, nullable=False, default=0)
    events_detected = Column(Integer, nullable=False, default=0)
    events_sent = Column(Integer, nullable=False, default=0)
    summaries_generated = Column(Integer, nullable=False, default=0)
    email_sent = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sent_records = relationship("SentNews", back_populates="run", lazy="raise")
