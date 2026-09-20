"""
app/graph/nodes/send_email.py
──────────────────────────────
Node 9: send_email
Sends the formatted digest email, records each sent event in the DB,
and updates the workflow_run record.
"""
from __future__ import annotations

from typing import Any
import uuid

import structlog

from app.config import settings
from app.database.connection import get_session
from app.database.repositories import SentNewsRepository, WorkflowRunRepository
from app.database.models import Event as EventORM, EventArticle
from app.graph.state import AgentState
from app.models.event import NewsEvent
from app.services.email import email_service

log = structlog.get_logger(__name__)


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default



async def _persist_events(events: list[NewsEvent], run_id: str) -> None:
    """Persist events and mark them as sent in the database."""
    async with get_session() as session:
        sent_repo = SentNewsRepository(session)
        run_uuid = uuid.UUID(run_id)

        for event in events:
            if not event.summary:
                continue
            try:
                async with session.begin_nested():
                    # Upsert event record
                    event_uuid = uuid.UUID(event.event_id)
                    event_orm = EventORM(
                        id=event_uuid,
                        canonical_title=event.canonical_title,
                        event_date=event.event_date,
                        category=event.category,
                        subcategory=event.subcategory,
                        canonical_url=event.canonical_url,
                        canonical_source_name=event.canonical_source_name,
                        summary=event.summary.summary_text if event.summary else None,
                        importance_score=event.importance_score,
                        verification_status=event.verification_status,
                        verification_source=event.verification_source,
                    )
                    session.add(event_orm)
                    await session.flush()

                    # Link articles to event
                    for article in event.articles:
                        ea = EventArticle(
                            event_id=event_uuid,
                            article_id=uuid.UUID(article.article_id),
                        )
                        session.add(ea)
                    await session.flush()

                    # Record as sent
                    await sent_repo.record_sent(
                        event_id=event_uuid,
                        recipient=settings.email_recipient,
                        run_id=run_uuid,
                    )
            except Exception as exc:
                log.error(
                    "send_email.persist_failed",
                    event_id=event.event_id,
                    error=str(exc),
                )


async def send_email(state: AgentState) -> AgentState:
    """
    LangGraph node: send_email
    Delivers the formatted email via SMTP.
    Records sent events in DB to prevent duplicate sends.
    """
    subject = state.get("email_subject", "")
    html = state.get("email_html", "")
    plain = state.get("email_plain", "")
    important_events = state.get("important_events", [])
    run_id = state["run_id"]

    # If no_news_behaviour=skip and no subject or no events was set, skip sending
    if not subject or (not important_events and settings.no_news_behaviour != "send_empty"):
        log.info(
            "email.delivery_skipped",
            reason="no_important_news",
            message="Email delivery was skipped because no important news was found for this run.",
        )
        stats = dict(state.get("stats", {}))
        stats["email_sent"] = False
        stats["events_sent"] = 0

        if run_id:
            try:
                async with get_session() as session:
                    run_repo = WorkflowRunRepository(session)
                    await run_repo.complete(
                        run_id=uuid.UUID(run_id),
                        articles_collected=_to_int(stats.get("articles_collected")),
                        articles_relevant=_to_int(stats.get("articles_relevant")),
                        articles_deduplicated=_to_int(stats.get("articles_deduplicated")),
                        articles_verified=_to_int(stats.get("articles_verified")),
                        events_detected=_to_int(stats.get("events_detected")),
                        events_sent=0,
                        summaries_generated=0,
                        email_sent=False,
                        error_message=None,
                    )
            except Exception as exc:
                log.error("send_email.run_complete_failed", error=str(exc))

        return {
            **state,
            "email_sent": False,
            "stats": stats,
        }

    log.info(
        "node.send_email.start",
        recipient=settings.email_recipient,
        event_count=len(important_events),
    )

    email_sent = False
    errors = list(state.get("errors", []))

    try:
        email_service.send(
            subject=subject,
            html_body=html,
            plain_body=plain,
            recipient=settings.email_recipient,
        )
        email_sent = True
        log.info("node.send_email.success")
    except Exception as exc:
        msg = f"Email delivery failed: {exc}"
        log.error("node.send_email.failed", error=msg)
        errors.append(msg)

    # Persist sent events to DB (even if email delivery had issues — prevents future re-send)
    if email_sent and important_events:
        try:
            await _persist_events(important_events, run_id)
        except Exception as exc:
            msg = f"DB persist sent events failed: {exc}"
            log.error("send_email.db_failed", error=msg)
            errors.append(msg)

    # Finalise workflow_run record
    stats = dict(state.get("stats", {}))
    stats["email_sent"] = email_sent
    stats["events_sent"] = len(important_events) if email_sent else 0

    try:
        async with get_session() as session:
            run_repo = WorkflowRunRepository(session)
            await run_repo.complete(
                run_id=uuid.UUID(run_id),
                articles_collected=_to_int(stats.get("articles_collected")),
                articles_relevant=_to_int(stats.get("articles_relevant")),
                articles_deduplicated=_to_int(stats.get("articles_deduplicated")),
                articles_verified=_to_int(stats.get("articles_verified")),
                events_detected=_to_int(stats.get("events_detected")),
                events_sent=_to_int(stats.get("events_sent")),
                summaries_generated=_to_int(stats.get("summaries_generated")),
                email_sent=email_sent,
                error_message="; ".join(errors) if errors else None,
            )
    except Exception as exc:
        log.error("send_email.run_complete_failed", error=str(exc))

    return {
        **state,
        "email_sent": email_sent,
        "errors": errors,
        "stats": stats,
    }
