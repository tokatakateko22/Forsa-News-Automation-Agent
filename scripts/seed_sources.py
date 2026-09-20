"""
scripts/seed_sources.py
───────────────────────
Seeds the `sources` table with Tier 1 (official) and Tier 2 (reputable media) sources.
Run once after database initialisation, or re-run to update existing entries.

Usage:
    py -3 scripts/seed_sources.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.database.connection import get_session
from app.database.models import Source
from app.database.repositories import SourceRepository

# ── Source definitions ────────────────────────────────────────────────────────
# Format: (name, url, source_type, tier, priority, language, category_hint)

SOURCES: list[dict] = [
    # ── Tier 1: Official / Primary Sources ───────────────────────────────────
    {
        "name": "Central Bank of Egypt",
        "url": "https://www.cbe.org.eg/",
        "source_type": "scrape",
        "tier": 1,
        "priority": 10,
        "language": "ar",
        "category_hint": "CBE",
    },
    {
        "name": "Central Bank of Egypt (EN)",
        "url": "https://www.cbe.org.eg/en/",
        "source_type": "scrape",
        "tier": 1,
        "priority": 10,
        "language": "en",
        "category_hint": "CBE",
    },
    {
        "name": "Financial Regulatory Authority",
        "url": "https://fra.gov.eg/",
        "source_type": "scrape",
        "tier": 1,
        "priority": 10,
        "language": "ar",
        "category_hint": "FRA",
    },
    {
        "name": "Financial Regulatory Authority (EN)",
        "url": "https://fra.gov.eg/en/",
        "source_type": "scrape",
        "tier": 1,
        "priority": 10,
        "language": "en",
        "category_hint": "FRA",
    },
    # ── Tier 2: Reputable Financial Media ─────────────────────────────────────
    {
        "name": "Reuters Egypt",
        "url": "https://www.reuters.com/",
        "source_type": "api",
        "tier": 2,
        "priority": 20,
        "language": "en",
        "category_hint": "General",
    },
    {
        "name": "Bloomberg",
        "url": "https://www.bloomberg.com/",
        "source_type": "api",
        "tier": 2,
        "priority": 20,
        "language": "en",
        "category_hint": "General",
    },
    # ── Tier 2: Reputable Financial Media & Feeds ────────────────────────────
    {
        "name": "Al Borsa News",
        "url": "https://alborsaanews.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 15,
        "language": "ar",
        "category_hint": "Financial Market",
    },
    {
        "name": "Hapi Journal",
        "url": "https://hapijournal.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 15,
        "language": "ar",
        "category_hint": "Banking",
    },
    {
        "name": "Economy Plus",
        "url": "https://economyplusme.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 20,
        "language": "ar",
        "category_hint": "Economy",
    },
    {
        "name": "Daily News Egypt RSS",
        "url": "https://dailynewsegypt.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 25,
        "language": "en",
        "category_hint": "General",
    },
    {
        "name": "EnterpriseAM RSS",
        "url": "https://enterpriseam.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 15,
        "language": "en",
        "category_hint": "General",
    },
    {
        "name": "ME Observer Financial RSS",
        "url": "https://meobserver.news/business-economix/market-updates-finance/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 15,
        "language": "en",
        "category_hint": "Financial Market",
    },
    {
        "name": "Egypt Independent RSS",
        "url": "https://www.egyptindependent.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 25,
        "language": "en",
        "category_hint": "General",
    },
    {
        "name": "Amwal Al Ghad RSS",
        "url": "https://amwalalghad.com/feed/",
        "source_type": "rss",
        "tier": 2,
        "priority": 25,
        "language": "ar",
        "category_hint": "General",
    },
]


async def seed_sources() -> None:
    print("Seeding sources...")
    async with get_session() as session:
        repo = SourceRepository(session)
        active_names = {s["name"] for s in SOURCES}
        created = 0
        updated = 0
        for s in SOURCES:
            existing = await repo.get_by_name(s["name"])
            source_obj = Source(**s)
            if existing:
                existing.url = s["url"]
                existing.tier = s["tier"]
                existing.priority = s["priority"]
                setattr(existing, "active", True)
                updated += 1
            else:
                session.add(source_obj)
                created += 1

        all_active = await repo.get_active_sources()
        deactivated = 0
        for src in all_active:
            if src.name not in active_names:
                setattr(src, "active", False)
                deactivated += 1

    print(f"Done — {created} created, {updated} updated, {deactivated} deactivated.")


if __name__ == "__main__":
    asyncio.run(seed_sources())
