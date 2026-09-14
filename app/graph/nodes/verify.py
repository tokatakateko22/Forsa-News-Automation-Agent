"""
app/graph/nodes/verify.py
──────────────────────────
Node 5: verify_sources
Assigns OFFICIAL / VERIFIED / UNVERIFIED status to each event.
Pure deterministic logic — no LLM call.
"""
from __future__ import annotations

import structlog

from app.graph.state import AgentState
from app.services.verification import VerificationService

log = structlog.get_logger(__name__)


async def verify_sources(state: AgentState) -> AgentState:
    """
    LangGraph node: verify_sources
    Checks source URLs against official and reputable media pattern lists.
    """
    events = state["events"]
    log.info("node.verify.start", event_count=len(events))

    service = VerificationService()
    verified_events = service.verify_batch(events)

    official = sum(1 for e in verified_events if e.verification_status == "OFFICIAL")
    verified = sum(1 for e in verified_events if e.verification_status == "VERIFIED")
    unverified = sum(1 for e in verified_events if e.verification_status == "UNVERIFIED")

    log.info(
        "node.verify.done",
        official=official,
        verified=verified,
        unverified=unverified,
    )

    stats = dict(state.get("stats", {}))
    stats["articles_verified"] = official + verified

    return {
        **state,
        "events": verified_events,
        "stats": stats,
    }
