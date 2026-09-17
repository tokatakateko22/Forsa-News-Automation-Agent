"""
app/main.py
────────────
Entry point for the Forsa Financial News Monitoring Agent.

Responsibilities:
  1. Initialise structured logging
  2. Set up APScheduler with timezone-aware schedule from configuration
  3. Provide --run-now CLI flag for manual/test execution
  4. Run the LangGraph pipeline and track execution windows

Schedule is fully configurable — no business logic changes needed to switch
from daily → hourly → twice_daily → custom_cron.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database.connection import get_session, close_db, init_db
from app.database.repositories import WorkflowRunRepository
from app.graph.graph import pipeline
from app.graph.state import AgentState, RunStats

# ── Logging setup ─────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer() if settings.environment != "production"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)


# ── Collection window calculation ─────────────────────────────────────────────

import socket
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

@retry(
    retry=retry_if_exception_type((OSError, socket.gaierror)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _get_collection_window(lookback_days: int | None = None) -> tuple[datetime, datetime]:
    """
    Determine the [start, end] window for news retrieval.
    If lookback_days is passed (e.g. 7 for a weekly test), start = end - timedelta(days=lookback_days).
    Otherwise:
      start = last_successful_run_time - overlap_hours
      end   = now
    If no prior run exists, uses initial_lookback_hours (168h for weekly).
    """
    end_time = datetime.now(timezone.utc)

    if lookback_days is not None:
        start_time = end_time - timedelta(days=lookback_days)
        log.info(
            "window.manual_lookback",
            days=lookback_days,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )
        return start_time, end_time

    async with get_session() as session:
        run_repo = WorkflowRunRepository(session)
        last_run = await run_repo.get_last_successful_run_time()

    if last_run:
        # Apply overlap to catch delayed publications
        start_time = last_run - timedelta(hours=settings.overlap_hours)
        # Ensure collection window always spans at least initial_lookback_hours
        min_lookback = end_time - timedelta(hours=settings.initial_lookback_hours)
        if start_time > min_lookback:
            start_time = min_lookback
        log.info(
            "window.from_last_run",
            last_run=last_run.isoformat(),
            start_time=start_time.isoformat(),
        )
    else:
        # First ever run — look back by initial_lookback_hours
        start_time = end_time - timedelta(hours=settings.initial_lookback_hours)
        log.info(
            "window.initial_lookback",
            hours=settings.initial_lookback_hours,
            start_time=start_time.isoformat(),
        )

    return start_time, end_time


@retry(
    retry=retry_if_exception_type((OSError, socket.gaierror)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _record_run_start(run_id: uuid.UUID) -> None:
    async with get_session() as session:
        run_repo = WorkflowRunRepository(session)
        await run_repo.create(run_id=run_id)


# ── Pipeline runner ───────────────────────────────────────────────────────────

async def run_pipeline(
    lookback_days: int | None = None,
    ignore_already_sent: bool = False,
    force_fallback: bool = False,
) -> dict:
    """Execute a single complete pipeline run."""
    run_uuid = uuid.uuid4()
    run_id = str(run_uuid)
    log.info(
        "pipeline.start",
        run_id=run_id,
        lookback_days=lookback_days,
        ignore_sent=ignore_already_sent,
        force_fallback=force_fallback,
    )

    # Create workflow_run record with automatic retry on transient network hiccups
    await _record_run_start(run_uuid)

    try:
        collection_start, collection_end = await _get_collection_window(lookback_days=lookback_days)

        initial_state: AgentState = {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc),
            "collection_start": collection_start,
            "collection_end": collection_end,
            "ignore_already_sent": ignore_already_sent,
            "force_search_fallback": force_fallback,
            "raw_articles": [],
            "clean_articles": [],
            "classifications": {},
            "relevant_articles": [],
            "events": [],
            "important_events": [],
            "email_subject": "",
            "email_html": "",
            "email_plain": "",
            "email_sent": False,
            "errors": [],
            "stats": RunStats(
                articles_collected=0,
                articles_preprocessed=0,
                articles_relevant=0,
                articles_deduplicated=0,
                articles_verified=0,
                events_detected=0,
                events_sent=0,
                summaries_generated=0,
                email_sent=False,
                errors=[],
            ),
        }

        # Invoke the LangGraph pipeline
        final_state = await pipeline.ainvoke(initial_state)

        log.info(
            "pipeline.complete",
            run_id=run_id,
            email_sent=final_state.get("email_sent"),
            events_sent=final_state.get("stats", {}).get("events_sent", 0),
            errors=final_state.get("errors", []),
        )
        return final_state

    except Exception as exc:
        log.error("pipeline.fatal_error", run_id=run_id, error=str(exc), exc_info=True)
        return {"errors": [str(exc)]}


# ── Scheduler configuration ───────────────────────────────────────────────────

def _build_trigger() -> list[CronTrigger]:
    """
    Build APScheduler CronTrigger(s) from configuration.
    Supports: daily | hourly | twice_daily | custom_cron
    """
    tz = pytz.timezone(settings.timezone)
    freq = settings.schedule_frequency

    if freq == "daily":
        return [
            CronTrigger(
                hour=settings.schedule_hour,
                minute=settings.schedule_minute,
                timezone=tz,
            )
        ]
    elif freq == "hourly":
        return [CronTrigger(minute=0, timezone=tz)]
    elif freq == "twice_daily":
        return [
            CronTrigger(
                hour=settings.schedule_hour,
                minute=settings.schedule_minute,
                timezone=tz,
            ),
            CronTrigger(
                hour=settings.schedule_hour_2,
                minute=settings.schedule_minute_2,
                timezone=tz,
            ),
        ]
    elif freq == "weekly":
        return [
            CronTrigger(
                day_of_week=settings.schedule_day_of_week,
                hour=settings.schedule_hour,
                minute=settings.schedule_minute,
                timezone=tz,
            )
        ]
    elif freq == "custom_cron":
        parts = settings.schedule_cron.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {settings.schedule_cron!r}")
        minute, hour, day, month, day_of_week = parts
        return [
            CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=tz,
            )
        ]
    else:
        raise ValueError(f"Unknown SCHEDULE_FREQUENCY: {freq!r}")


async def _scheduled_run() -> None:
    """Wrapper called by the scheduler."""
    log.info("scheduler.triggered", frequency=settings.schedule_frequency)
    await run_pipeline()


async def start_scheduler() -> None:
    """Configure and start the APScheduler."""
    scheduler = AsyncIOScheduler()
    triggers = _build_trigger()

    for i, trigger in enumerate(triggers):
        scheduler.add_job(
            _scheduled_run,
            trigger=trigger,
            id=f"forsa_news_pipeline_{i}",
            name=f"Forsa News Pipeline ({settings.schedule_frequency})",
            max_instances=1,           # Prevent overlapping runs
            misfire_grace_time=3600,   # Allow up to 1h late start
        )

    scheduler.start()
    log.info(
        "scheduler.started",
        frequency=settings.schedule_frequency,
        time=settings.schedule_time,
        timezone=settings.timezone,
    )

    # Keep the event loop running
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        await close_db()
        log.info("scheduler.stopped")


# ── CLI entry point ───────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Financial News Monitoring Agent"
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Execute one pipeline run immediately and exit.",
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Run for today only (past 24 hours).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Lookback window in days (e.g. --days 7 for past week).",
    )
    parser.add_argument(
        "--ignore-sent",
        action="store_true",
        help="Bypass database check for already-sent articles (for testing).",
    )
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        help="Bypass SerpAPI and force fallback retrieval (for testing).",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialise database tables and exit.",
    )
    args = parser.parse_args()

    if args.init_db:
        log.info("db.initialising")
        await init_db()
        await close_db()
        log.info("db.initialised")
        return

    if args.run_now:
        effective_days = 1 if args.today else args.days
        log.info(
            "manual_run.start",
            days=effective_days,
            ignore_sent=args.ignore_sent,
            force_fallback=args.force_fallback,
        )
        await run_pipeline(
            lookback_days=effective_days,
            ignore_already_sent=args.ignore_sent,
            force_fallback=args.force_fallback,
        )
        await close_db()
        return

    # Normal mode: start the scheduler
    await start_scheduler()


if __name__ == "__main__":
    asyncio.run(main())
