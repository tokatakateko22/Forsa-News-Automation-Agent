"""
app/graph/nodes/format_email.py
────────────────────────────────
Node 8: format_email
Assembles the CEO digest email from event summaries.

CEO-facing output contains ONLY:
  - Factual summary text
  - Category label
  - Source name
  - Publication date/time
  - Original source URL

CEO output NEVER contains:
  - Importance scores
  - Relevance scores
  - Classification confidence
  - AI labels or internal reasoning
  - Business impact analysis
  - Recommendations
"""
import uuid
from datetime import datetime, timezone

import structlog
import pytz

from app.config import settings
from app.database.connection import get_session
from app.database.repositories import WorkflowRunRepository
from app.graph.state import AgentState
from app.models.event import NewsEvent

log = structlog.get_logger(__name__)

# Category display order (CRITICAL categories first)
CATEGORY_ORDER = [
    "CBE",
    "FRA",
    "Consumer Finance",
    "Competitor",
    "FinTech",
    "Banking",
    "Financial Market",
    "Economy",
    "Other",
]

# Category emoji indicators
CATEGORY_EMOJI = {
    "CBE": "🔴",
    "FRA": "🔴",
    "Consumer Finance": "🟠",
    "Competitor": "🟠",
    "FinTech": "🟡",
    "Banking": "🟡",
    "Financial Market": "🟡",
    "Economy": "🟡",
    "Other": "⚪",
}


def _format_date(dt: datetime | None) -> str:
    if not dt:
        return "Date unknown"
    try:
        tz = pytz.timezone(settings.timezone)
        local = dt.astimezone(tz)
        return local.strftime("%d %b %Y, %H:%M %Z")
    except Exception:
        return dt.strftime("%d %b %Y, %H:%M UTC")


def _event_plain_block(event: NewsEvent) -> str:
    """Plain text block for one event."""
    summary = event.summary
    if not summary:
        return ""

    emoji = CATEGORY_EMOJI.get(event.category, "⚪")
    category_label = event.category
    if event.category == "Competitor" and summary.competitor_name:
        category_label = f"Competitor — {summary.competitor_name}"
    elif event.subcategory:
        category_label = f"{event.category} — {event.subcategory}"

    lines = [
        "━" * 54,
        f"{emoji} {category_label}",
        "",
        summary.summary_text,
        "",
        f"Source: {summary.source_name}",
        f"Published: {_format_date(summary.published_at)}",
        f"Link: {summary.canonical_url}",
    ]
    return "\n".join(lines)


def _event_html_block(event: NewsEvent) -> str:
    """HTML block for one event."""
    summary = event.summary
    if not summary:
        return ""

    emoji = CATEGORY_EMOJI.get(event.category, "⚪")
    category_label = event.category
    if event.category == "Competitor" and summary.competitor_name:
        category_label = f"Competitor — {summary.competitor_name}"
    elif event.subcategory:
        category_label = f"{event.category} — {event.subcategory}"

    color_map = {
        "🔴": "#e53e3e",
        "🟠": "#dd6b20",
        "🟡": "#d69e2e",
        "⚪": "#718096",
    }
    accent = color_map.get(emoji, "#718096")

    return f"""
<div style="border-left:4px solid {accent};padding:16px 20px;margin:16px 0;background:#f9f9f9;">
  <p style="margin:0 0 6px 0;font-weight:700;font-size:14px;color:{accent};">{emoji} {category_label}</p>
  <p style="margin:0 0 12px 0;font-size:15px;line-height:1.6;color:#2d3748;">{summary.summary_text}</p>
  <p style="margin:0;font-size:12px;color:#718096;">
    <strong>Source:</strong> {summary.source_name} &nbsp;|&nbsp;
    <strong>Published:</strong> {_format_date(summary.published_at)} &nbsp;|&nbsp;
    <a href="{summary.canonical_url}" style="color:#3182ce;">View Article →</a>
  </p>
</div>
"""


def _build_email(events: list[NewsEvent], run_date: str) -> tuple[str, str, str]:
    """
    Build subject, HTML body, and plain-text body from the list of events.
    Returns (subject, html, plain).
    """
    subject = f"{settings.email_subject_prefix} — {run_date}"

    # Sort events by category priority
    def sort_key(e: NewsEvent) -> int:
        try:
            return CATEGORY_ORDER.index(e.category)
        except ValueError:
            return len(CATEGORY_ORDER)

    sorted_events = sorted(events, key=sort_key)

    # ── Plain text ────────────────────────────────────────────────────────────
    plain_lines = [
        f"Forsa Financial Market News",
        f"{run_date}",
        "",
    ]
    for event in sorted_events:
        block = _event_plain_block(event)
        if block:
            plain_lines.append(block)
            plain_lines.append("")
    plain_lines.append("━" * 54)
    plain_lines.append("This is an automated news digest. Do not reply.")
    plain_body = "\n".join(plain_lines)

    # ── HTML ──────────────────────────────────────────────────────────────────
    event_html = "".join(_event_html_block(e) for e in sorted_events if e.summary)

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#2d3748;">
  <div style="border-bottom:3px solid #1a365d;padding-bottom:12px;margin-bottom:20px;">
    <h1 style="margin:0;font-size:22px;color:#1a365d;">Forsa Financial Market News</h1>
    <p style="margin:4px 0 0 0;font-size:13px;color:#718096;">{run_date}</p>
  </div>
  {event_html}
  <div style="border-top:1px solid #e2e8f0;margin-top:24px;padding-top:12px;
              font-size:11px;color:#a0aec0;text-align:center;">
    Automated news digest · Do not reply
  </div>
</body>
</html>"""

    return subject, html_body, plain_body


def _build_no_news_email(run_date: str) -> tuple[str, str, str]:
    """Build a minimal email for when no significant news was found."""
    subject = f"{settings.email_subject_prefix} — {run_date} — No Significant News"
    plain = (
        f"Forsa Financial Market News\n{run_date}\n\n"
        "No significant Egyptian financial or consumer-finance news was identified "
        "in today's monitoring window.\n\n"
        "━" * 54 + "\n"
        "Automated news digest · Do not reply"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<body style="font-family:Arial,Helvetica,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
  <h1 style="color:#1a365d;">Forsa Financial Market News</h1>
  <p style="color:#718096;">{run_date}</p>
  <p style="color:#4a5568;">No significant Egyptian financial or consumer-finance news was
  identified in today's monitoring window.</p>
  <p style="font-size:11px;color:#a0aec0;">Automated news digest · Do not reply</p>
</body>
</html>"""
    return subject, html, plain


async def format_email(state: AgentState) -> AgentState:
    """
    LangGraph node: format_email
    Assembles the CEO digest email. No internal scores or AI labels included.
    """
    important_events = state.get("important_events", [])
    tz = pytz.timezone(settings.timezone)
    run_date = datetime.now(tz).strftime("%d %B %Y")

    if not important_events and settings.no_news_behaviour != "send_empty":
        log.info(
            "format_email.skip",
            reason="no_important_news",
            message="Formatting skipped: no important news found and NO_NEWS_BEHAVIOUR is skip.",
        )
        return {
            **state,
            "email_subject": "",
            "email_html": "",
            "email_plain": "",
            "email_sent": False,
        }

    log.info("node.format_email.start", events=len(important_events))

    if important_events:
        subject, html, plain = _build_email(important_events, run_date)
    else:
        subject, html, plain = _build_no_news_email(run_date)

    log.info("node.format_email.done", subject=subject)

    return {
        **state,
        "email_subject": subject,
        "email_html": html,
        "email_plain": plain,
    }


async def handle_no_news(state: AgentState) -> AgentState:
    """
    LangGraph node: handle_no_news
    Called when no important events were found.
    If NO_NEWS_BEHAVIOUR is skip:
      - Skips formatting and sending email
      - Does not call Power Automate webhook
      - Sets email_sent to False
      - Completes workflow run in DB with zero events sent
      - Logs clear message explaining delivery was skipped
    If NO_NEWS_BEHAVIOUR is send_empty:
      - Builds empty-run email for send_email node
    """
    tz = pytz.timezone(settings.timezone)
    run_date = datetime.now(tz).strftime("%d %B %Y")

    if settings.no_news_behaviour == "send_empty":
        subject, html, plain = _build_no_news_email(run_date)
        return {
            **state,
            "important_events": [],
            "email_subject": subject,
            "email_html": html,
            "email_plain": plain,
        }

    # Default production behaviour: skip delivery
    log.info(
        "email.delivery_skipped",
        reason="no_important_news",
        message="Email delivery was skipped because no important news was found for this run.",
    )

    stats = dict(state.get("stats", {}))
    stats["email_sent"] = False
    stats["events_sent"] = 0

    run_id = state.get("run_id")
    if run_id:
        try:
            async with get_session() as session:
                run_repo = WorkflowRunRepository(session)
                await run_repo.complete(
                    run_id=uuid.UUID(run_id),
                    articles_collected=stats.get("articles_collected", 0),
                    articles_relevant=stats.get("articles_relevant", 0),
                    articles_deduplicated=stats.get("articles_deduplicated", 0),
                    articles_verified=stats.get("articles_verified", 0),
                    events_detected=stats.get("events_detected", 0),
                    events_sent=0,
                    summaries_generated=0,
                    email_sent=False,
                    error_message=None,
                )
        except Exception as exc:
            log.error("handle_no_news.db_complete_failed", error=str(exc))

    return {
        **state,
        "important_events": [],
        "email_subject": "",
        "email_html": "",
        "email_plain": "",
        "email_sent": False,
        "stats": stats,
    }


def route_after_no_news(state: AgentState) -> str:
    """
    Conditional edge: determines next step after handle_no_news.
    If NO_NEWS_BEHAVIOUR is send_empty, proceeds to format_email -> send_email.
    Otherwise, terminates workflow immediately at END.
    """
    if settings.no_news_behaviour == "send_empty":
        return "format_email"
    return "end"
