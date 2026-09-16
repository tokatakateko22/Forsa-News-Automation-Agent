"""
app/graph/nodes/filter.py
──────────────────────────
Node 6: filter_important_news
  1. Scores each event with LLM importance scoring (Gemini Flash)
  2. Applies IMPORTANCE_THRESHOLD
  3. Checks sent_news table — skips already-sent events (idempotency)
  4. Routes to either 'has_news' or 'no_news'

Internal scores (importance_score, relevance_score) are NEVER exposed to CEO.
"""
from __future__ import annotations

import asyncio
import uuid

import structlog

from app.config import settings
from app.database.connection import get_session
from app.database.repositories import SentNewsRepository
from app.graph.state import AgentState
from app.models.event import NewsEvent
from app.services import llm

log = structlog.get_logger(__name__)

_SEMAPHORE = asyncio.Semaphore(1)


async def _score_event(event: NewsEvent) -> NewsEvent:
    """Score event importance with Gemini Flash if not already scored during classification."""
    if event.importance_score > 0:
        return event

    async with _SEMAPHORE:
        await asyncio.sleep(2.0)  # Pacing for Gemini
        try:
            scores = await llm.score_importance(
                title=event.canonical_title,
                content=event.articles[0].content or "" if event.articles else "",
                category=event.category,
            )
            event.importance_score = scores["importance_score"]
            event.relevance_score = scores["relevance_score"]
        except Exception as exc:
            log.warning(
                "filter.score_failed",
                event_id=event.event_id,
                error=str(exc),
            )
    return event


_FRA_CONSUMER_FINANCE_TERMS = [
    "consumer finance", "consumer credit", "retail lending", "bnpl", "buy now pay later",
    "installment", "instalment", "i-score", "iscore", "credit reporting", "credit bureau",
    "otp", "e-kyc", "ekyc", "debt burden", "microfinance", "sme finance",
    "behavioural analysis", "behavioral analysis", "credit scoring",
    "تمويل استهلاكي", "تمويل الاستهلاك", "تمويل استهلاك", "تمويل الافراد", "تمويل أفراد", "تقسيط", "التقسيط",
    "الشراء الآن والدفع لاحقاً", "الشراء الان والدفع لاحقا",
    "اي سكور", "آي سكور", "استعلام ائتماني", "الربط اللحظي",
    "رمز التحقق", "التحقق من هوية العملاء", "التحقق من هويه العملاء",
    "سقف عبء الدين", "عبء الدين",
    "تمويل المشروعات المتوسطة والصغيرة", "تمويل المشروعات المتوسطه والصغيره", "متناهي الصغر",
    "التحليل السلوكي",
    "فاليو", "حالا", "كونتكت", "أمان", "امان", "سهولة", "سهوله", "سيمبل", "بلنك", "فرصة", "فرصه", "أولين", "اولين",
]


def _is_fra_consumer_finance(text: str) -> bool:
    norm = text.lower()
    return any(term in norm for term in _FRA_CONSUMER_FINANCE_TERMS)


def _has_substance(event: NewsEvent) -> bool:
    """Validate that the event has sufficient concrete content and is in scope for a CEO."""
    from app.graph.nodes.classify import _EXCLUSION_RE
    if _EXCLUSION_RE.search(event.canonical_title):
        return False

    # Check total length of content across articles
    total_content = " ".join((a.content or "") for a in event.articles).strip()
    # If the combined content across all articles is under 80 characters, drop as an unsubstantiated stub
    if len(total_content) < 80:
        return False

    if event.category == "Other":
        return False

    # Strictly enforce that FRA news must be related to consumer financing
    if event.category == "FRA":
        combined_text = f"{event.canonical_title} {total_content}"
        if not _is_fra_consumer_finance(combined_text):
            return False

    return True


async def filter_important_news(state: AgentState) -> AgentState:
    """
    LangGraph node: filter_important_news
    Scores events, applies thresholds, checks DB for already-sent events.
    Populates important_events list.
    """
    events = state["events"]
    log.info("node.filter.start", event_count=len(events))

    # Score all events concurrently
    tasks = [_score_event(e) for e in events]
    scored_events = await asyncio.gather(*tasks)

    # Apply importance threshold and executive substance check
    above_threshold = [
        e for e in scored_events
        if e.importance_score >= settings.importance_threshold and _has_substance(e)
    ]
    log.info(
        "node.filter.threshold",
        above=len(above_threshold),
        below=len(scored_events) - len(above_threshold),
        threshold=settings.importance_threshold,
    )

    # Check DB for already-sent events (idempotency guard)
    ignore_already_sent = state.get("ignore_already_sent", False)
    important_events: list[NewsEvent] = []
    async with get_session() as session:
        sent_repo = SentNewsRepository(session)
        for event in above_threshold:
            if not ignore_already_sent:
                try:
                    article_urls = [a.url for a in event.articles if a.url]
                    already = await sent_repo.is_event_already_sent(
                        event_id=uuid.UUID(event.event_id) if event.event_id else None,
                        canonical_url=event.canonical_url,
                        canonical_title=event.canonical_title,
                        article_urls=article_urls,
                    )
                    if already:
                        log.info(
                            "filter.already_sent",
                            event_id=event.event_id,
                            title=event.canonical_title[:60],
                        )
                        continue
                except Exception as exc:
                    log.warning("filter.db_check_failed", error=str(exc))

            event.should_send = True
            important_events.append(event)

    log.info(
        "node.filter.done",
        important_events=len(important_events),
    )

    return {
        **state,
        "important_events": important_events,
    }


def route_after_filter(state: AgentState) -> str:
    """
    Conditional edge: determines next node after filter_important_news.
    Returns 'has_news' if there are events to send, 'no_news' otherwise.
    """
    if state.get("important_events"):
        return "has_news"
    return "no_news"
