"""
scripts/run_once.py
────────────────────
Manually trigger a single pipeline run.
Useful for testing and development.

Usage:
    # Test with default lookback (7 days if weekly):
    py -3 scripts/run_once.py

    # Explicitly test last 7 days and ignore previously sent events:
    py -3 scripts/run_once.py --days 7 --ignore-sent
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.config import settings
from app.main import run_pipeline
from app.database.connection import close_db


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Forsa News Agent once manually.")
    default_days = 7 if settings.schedule_frequency == "weekly" else None
    parser.add_argument(
        "--today",
        action="store_true",
        help="Run for today only (past 24 hours).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=default_days,
        help=f"Lookback window in days (default: {default_days or 'calculated from DB'}).",
    )
    parser.add_argument(
        "--ignore-sent",
        action="store_true",
        help="Bypass database check for already-sent articles (useful for testing).",
    )
    parser.add_argument(
        "--force-fallback",
        action="store_true",
        help="Bypass SerpAPI and force fallback retrieval (useful for testing).",
    )
    args = parser.parse_args()

    effective_days = 1 if args.today else args.days

    print("=" * 65)
    print("Forsa News Agent — Manual Pipeline Run")
    print(f"Schedule Mode:     {settings.schedule_frequency.upper()}")
    print(f"Lookback Window:   {'TODAY ONLY (Last 24h)' if args.today or effective_days == 1 else f'{effective_days} days'}")
    print(f"Ignore Sent News:  {args.ignore_sent}")
    print(f"Force Fallback:    {args.force_fallback}")
    print(f"Recipient:         {settings.email_recipient}")
    print("=" * 65)
    print("Starting pipeline run...")
    try:
        await run_pipeline(
            lookback_days=effective_days,
            ignore_already_sent=args.ignore_sent,
            force_fallback=args.force_fallback,
        )
    finally:
        await close_db()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
