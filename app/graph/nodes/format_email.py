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
from typing import Any
import uuid
from datetime import datetime, timezone

import structlog
import pytz

from app.config import settings
from app.database.connection import get_session
from app.database.repositories import WorkflowRunRepository
from app.graph.state import AgentState
from app.models.event import NewsEvent
from app.services.spelling import normalize_brand_spellings

log = structlog.get_logger(__name__)


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default

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


ARABIC_COMPETITOR_MAP = {
    "فاليو": "valU",
    "حالا": "MNT-Halan",
    "ام ان تي حالا": "MNT-Halan",
    "كونتكت": "Contact Financial",
    "أمان": "Aman",
    "امان": "Aman",
    "سهولة": "Souhoola",
    "سيمبل": "Sympl",
    "بلنك": "Blnk",
    "شهري": "Shahry",
    "بي تك": "B.Tech",
    "موبايلي": "Mobily Pay",
    "اورنچ كاش": "Orange Cash",
    "اورانج كاش": "Orange Cash",
    "فودافون كاش": "Vodafone Cash",
    "اتصالات كاش": "Etisalat Cash",
    "وي باي": "WE Pay",
    "انستاباي": "InstaPay",
    "انستا باي": "InstaPay",
    "اي سكور": "I-Score",
    "آي سكور": "I-Score",
    "فوري": "Fawry",
    "إي فاينانس": "e-finance",
    "اي فاينانس": "e-finance",
}


def _format_category_label(event: NewsEvent, summary) -> str:
    """Return a clean English category label."""
    sub = summary.subcategory or event.subcategory or ""
    cat = event.category or "News"

    if cat == "Competitor":
        comp = getattr(summary, "competitor_name", None) or getattr(event, "competitor_match", None) or getattr(summary, "competitor_match", None)
        if not comp and sub:
            comp = ARABIC_COMPETITOR_MAP.get(sub, sub)
        if comp:
            clean_comp = ARABIC_COMPETITOR_MAP.get(comp, comp)
            return f"Competitor — {clean_comp}"
        return "Competitor — Market Activity"

    if sub:
        clean_sub = ARABIC_COMPETITOR_MAP.get(sub, sub)
        return f"{cat} — {clean_sub}"
    return cat


def _event_plain_block(event: NewsEvent) -> str:
    """Plain-text block for one event."""
    summary = event.summary
    if not summary:
        return ""

    emoji = CATEGORY_EMOJI.get(event.category, "⚪")
    category_label = normalize_brand_spellings(_format_category_label(event, summary))
    clean_text = normalize_brand_spellings(summary.summary_text, competitor_hint=summary.competitor_name)

    lines = [
        "━" * 54,
        f"{emoji} {category_label}",
        "",
        clean_text,
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
    category_label = normalize_brand_spellings(_format_category_label(event, summary))
    clean_text = normalize_brand_spellings(summary.summary_text, competitor_hint=summary.competitor_name)

    color_map = {
        "🏛️": "#2b6cb0",
        "🔴": "#e53e3e",
        "🏢": "#dd6b20",
        "🟠": "#dd6b20",
        "🟡": "#d69e2e",
        "📈": "#319795",
        "📊": "#4a5568",
        "🏦": "#2b6cb0",
        "⚪": "#718096",
    }
    accent = color_map.get(emoji, "#718096")

    return f"""
<div style="border-left:4px solid {accent};padding:16px 20px;margin:16px 0;background:#f9f9f9;">
  <p style="margin:0 0 6px 0;font-weight:700;font-size:14px;color:{accent};">{emoji} {category_label}</p>
  <p style="margin:0 0 12px 0;font-size:15px;line-height:1.6;color:#2d3748;">{clean_text}</p>
  <p style="margin:0;font-size:12px;color:#718096;">
    <strong>Source:</strong> {summary.source_name} &nbsp;|&nbsp;
    <strong>Published:</strong> {_format_date(summary.published_at)} &nbsp;|&nbsp;
    <a href="{summary.canonical_url}" style="color:#2b6cb0;font-weight:600;">View Article →</a>
  </p>
</div>
"""


# Executive Pillars in requested order: CBE -> Competitors -> Market Backdrop & Economy -> FRA
PILLARS = [
    {
        "id": "cbe",
        "title": "Central Bank of Egypt (CBE) — Macro & Monetary Policy",
        "emoji": "🏛️",
        "categories": ["CBE"],
    },
    {
        "id": "competitors",
        "title": "Competitor Intelligence & Consumer Finance",
        "emoji": "🏢",
        "categories": ["Competitor", "Consumer Finance", "FinTech"],
    },
    {
        "id": "market",
        "title": "Market Backdrop & Economy",
        "emoji": "📈",
        "categories": ["Financial Market", "Economy", "Banking", "Other"],
    },
    {
        "id": "fra",
        "title": "Financial Regulatory Authority (FRA) — Regulations & Market Oversight",
        "emoji": "🔴",
        "categories": ["FRA"],
    },
]


def _build_email(events: list[NewsEvent], run_date: str) -> tuple[str, str, str]:
    """
    Build subject, HTML body, and plain-text body from the list of events.
    Groups events into 4 executive pillars:
      1. Central Bank of Egypt (CBE)
      2. Financial Regulatory Authority (FRA)
      3. Competitor Intelligence & Consumer Finance
      4. Market Backdrop & Economy
    """
    subject = f"{settings.email_subject_prefix} — {run_date}"

    # Group events by pillar
    pillar_events: dict[str, list[NewsEvent]] = {p["id"]: [] for p in PILLARS}
    for event in events:
        placed = False
        for p in PILLARS:
            if event.category in p["categories"]:
                pillar_events[p["id"]].append(event)
                placed = True
                break
        if not placed:
            pillar_events["market"].append(event)

    # ── Plain text ────────────────────────────────────────────────────────────
    plain_lines = [
        f"Forsa Financial Market News",
        f"{run_date}",
        "",
    ]
    for p in PILLARS:
        group = pillar_events[p["id"]]
        if not group:
            continue
        plain_lines.append("═" * 54)
        plain_lines.append(f"{p['emoji']} {p['title']}")
        plain_lines.append("═" * 54)
        plain_lines.append("")
        for event in group:
            block = _event_plain_block(event)
            if block:
                plain_lines.append(block)
                plain_lines.append("")

    plain_lines.append("━" * 54)
    plain_lines.append("This is an automated news digest. Do not reply.")
    plain_body = "\n".join(plain_lines)

    # ── HTML ──────────────────────────────────────────────────────────────────
    html_sections = []
    for p in PILLARS:
        group = [e for e in pillar_events[p["id"]] if e.summary]
        if not group:
            continue
        section_heading = f"""
<div style="margin:28px 0 12px 0;padding-bottom:6px;border-bottom:2px solid #2b6cb0;">
  <h2 style="margin:0;font-size:15px;color:#2b6cb0;text-transform:uppercase;letter-spacing:0.5px;">
    {p['emoji']} {p['title']}
  </h2>
</div>
"""
        section_body = "".join(_event_html_block(e) for e in group)
        html_sections.append(section_heading + section_body)

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:0 auto;padding:20px;color:#2d3748;">
  <div style="border-bottom:3px solid #1a365d;padding-bottom:12px;margin-bottom:20px;">
    <h1 style="margin:0;font-size:22px;color:#1a365d;">Forsa Financial Market News</h1>
    <p style="margin:4px 0 0 0;font-size:13px;color:#718096;">{run_date}</p>
  </div>
  {"".join(html_sections)}
  <div style="border-top:1px solid #e2e8f0;margin-top:28px;padding-top:12px;
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
        "in the monitoring window.\n\n"
        "━" * 54 + "\n"
        "Automated news digest · Do not reply"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<body style="font-family:Arial,Helvetica,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
  <h1 style="color:#1a365d;">Forsa Financial Market News</h1>
  <p style="color:#718096;">{run_date}</p>
  <p style="color:#4a5568;">No significant Egyptian financial or consumer-finance news was
  identified in the monitoring window.</p>
  <p style="font-size:11px;color:#a0aec0;">Automated news digest · Do not reply</p>
</body>
</html>"""
    return subject, normalize_brand_spellings(html), normalize_brand_spellings(plain)


async def format_email(state: AgentState) -> AgentState:
    """
    LangGraph node: format_email
    Assembles the CEO digest email. No internal scores or AI labels included.
    """
    important_events = state.get("important_events", [])
    tz = pytz.timezone(settings.timezone)

    col_start = state.get("collection_start")
    col_end = state.get("collection_end")
    is_span_weekly = (
        (col_end - col_start).total_seconds() >= 86400 * 3
        if (col_start and col_end)
        else False
    )
    is_weekly = is_span_weekly or (
        settings.schedule_frequency == "weekly" and not (col_start and col_end)
    )

    if is_weekly and col_start and col_end:
        start_str = col_start.astimezone(tz).strftime("%d %b")
        end_str = col_end.astimezone(tz).strftime("%d %b %Y")
        run_date = f"Weekly Digest ({start_str} – {end_str})"
    else:
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
