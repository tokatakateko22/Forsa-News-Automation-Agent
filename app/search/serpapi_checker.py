"""
app/search/serpapi_checker.py
─────────────────────────────
Pre-flight verification for SerpAPI account status, quotas, and availability.
Determines whether SerpAPI has sufficient searches remaining or if fallback should take over.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"


@dataclass
class SerpApiStatus:
    is_available: bool
    reason: str
    searches_remaining: int = 0
    account_email: Optional[str] = None
    account_status: Optional[str] = None


async def check_serpapi_availability(
    api_key: Optional[str] = None,
    timeout: float = 8.0,
    force_fallback: Optional[bool] = None,
    min_searches: Optional[int] = None,
) -> SerpApiStatus:
    """
    Perform a pre-flight check on the SerpAPI account before running search queries.
    Detects quota exhaustion, HTTP 429 rate limits, invalid keys, and network issues.
    """
    effective_force = settings.force_search_fallback if force_fallback is None else force_fallback
    if effective_force:
        log.info("[SEARCH] Fallback forced by configuration.")
        return SerpApiStatus(
            is_available=False,
            reason="forced_by_configuration",
            searches_remaining=0,
        )

    key = (settings.serpapi_key if api_key is None else api_key or "").strip()
    if not key:
        log.warning("[SEARCH] SerpAPI key is empty or not configured.")
        return SerpApiStatus(
            is_available=False,
            reason="api_key_empty",
            searches_remaining=0,
        )

    threshold = settings.serpapi_min_searches if min_searches is None else min_searches

    log.info("[SEARCH] Checking SerpApi availability and quota...")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(SERPAPI_ACCOUNT_URL, params={"api_key": key})

        if resp.status_code == 200:
            data = resp.json()
            account_email = data.get("account_email")
            account_status = data.get("account_status", "")
            searches_left = data.get("total_searches_left")
            if searches_left is None:
                searches_left = data.get("plan_searches_left", 0) + data.get("extra_credits", 0)

            # Check if out of searches
            if searches_left <= 0 or "run out of searches" in account_status.lower():
                log.warning(
                    "[SEARCH] SerpApi quota exhausted.",
                    searches_left=searches_left,
                    account_status=account_status,
                )
                return SerpApiStatus(
                    is_available=False,
                    reason="quota_exhausted",
                    searches_remaining=searches_left,
                    account_email=account_email,
                    account_status=account_status,
                )

            if searches_left < threshold:
                log.warning(
                    "[SEARCH] SerpApi searches remaining below threshold.",
                    searches_left=searches_left,
                    threshold=threshold,
                )
                return SerpApiStatus(
                    is_available=False,
                    reason=f"insufficient_quota_{searches_left}_below_{threshold}",
                    searches_remaining=searches_left,
                    account_email=account_email,
                    account_status=account_status,
                )

            log.info(
                "[SEARCH] SerpApi quota available.",
                searches_remaining=searches_left,
                account_email=account_email,
            )
            return SerpApiStatus(
                is_available=True,
                reason="quota_available",
                searches_remaining=searches_left,
                account_email=account_email,
                account_status=account_status,
            )

        if resp.status_code == 429:
            log.warning("[SEARCH] SerpApi returned HTTP 429 (Too Many Requests / Quota Exhausted).")
            return SerpApiStatus(
                is_available=False,
                reason="rate_limited_429",
                searches_remaining=0,
            )

        if resp.status_code in (401, 403):
            log.warning("[SEARCH] SerpApi authentication error.", status_code=resp.status_code)
            return SerpApiStatus(
                is_available=False,
                reason="authentication_error",
                searches_remaining=0,
            )

        log.warning("[SEARCH] SerpApi returned unexpected HTTP status.", status_code=resp.status_code)
        return SerpApiStatus(
            is_available=False,
            reason=f"http_status_{resp.status_code}",
            searches_remaining=0,
        )

    except (httpx.TimeoutException, httpx.RequestError) as exc:
        log.warning("[SEARCH] SerpApi connection error.", error=str(exc))
        return SerpApiStatus(
            is_available=False,
            reason=f"connection_error: {exc}",
            searches_remaining=0,
        )
    except Exception as exc:
        log.warning("[SEARCH] Unexpected error checking SerpApi.", error=str(exc))
        return SerpApiStatus(
            is_available=False,
            reason=f"unexpected_error: {exc}",
            searches_remaining=0,
        )
