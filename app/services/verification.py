"""
app/services/verification.py
─────────────────────────────
Source verification service.
For each news event, attempts to locate an official source confirmation.

Priority:
  Tier 1 (CBE/FRA/official) → OFFICIAL
  Tier 2 (reputable media)  → VERIFIED
  Tier 3 only               → UNVERIFIED
"""
from __future__ import annotations

import structlog

from app.models.event import NewsEvent

log = structlog.get_logger(__name__)

# Sources that count as "official" confirmation
OFFICIAL_SOURCE_PATTERNS = [
    "cbe.org.eg",
    "fra.gov.eg",
    "cabinet.gov.eg",
    "mof.gov.eg",         # Ministry of Finance
    "presidency.eg",
    "sis.gov.eg",         # State Information Service
]

# Reputable financial media (Tier 2)
REPUTABLE_SOURCE_PATTERNS = [
    "reuters.com",
    "bloomberg.com",
    "zawya.com",
    "enterprise.press",
    "amwalalghad.com",
    "dailynewsegypt.com",
    "businessmonthly.net",
    "ahram.org.eg",
    "egypttoday.com",
    "egyptindependent.com",
    "mubasher.info",
]


def _url_matches(url: str, patterns: list[str]) -> bool:
    url_lower = url.lower()
    return any(p in url_lower for p in patterns)


class VerificationService:
    """
    Determines the verification status of a news event by examining
    the sources of all articles that cover it.
    """

    def verify(self, event: NewsEvent) -> NewsEvent:
        """
        Assigns verification_status and verification_source to the event.
        Returns the updated event (mutates in place).
        """
        # Check if any article comes from an official source
        for article in event.articles:
            if _url_matches(article.url, OFFICIAL_SOURCE_PATTERNS):
                event.verification_status = "OFFICIAL"
                event.verification_source = article.url
                log.debug(
                    "verification.official",
                    event_title=event.canonical_title[:60],
                    source=article.source_name,
                )
                return event

        # Check if canonical article is from Tier 1 (even if URL not matched above)
        if event.canonical_source_tier == 1:
            event.verification_status = "OFFICIAL"
            event.verification_source = event.canonical_url
            return event

        # Check for reputable Tier 2 source
        for article in event.articles:
            if article.source_tier <= 2 or _url_matches(article.url, REPUTABLE_SOURCE_PATTERNS):
                event.verification_status = "VERIFIED"
                event.verification_source = article.url
                log.debug(
                    "verification.verified",
                    event_title=event.canonical_title[:60],
                    source=article.source_name,
                )
                return event

        # Only Tier 3 sources
        event.verification_status = "UNVERIFIED"
        event.verification_source = None
        log.debug(
            "verification.unverified",
            event_title=event.canonical_title[:60],
        )
        return event

    def verify_batch(self, events: list[NewsEvent]) -> list[NewsEvent]:
        return [self.verify(e) for e in events]
