"""
scripts/run_once.py
────────────────────
Manually trigger a single pipeline run.
Useful for testing and development.

Usage:
    py -3 scripts/run_once.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.main import run_pipeline
from app.database.connection import close_db


async def main() -> None:
    print("Starting manual pipeline run...")
    try:
        await run_pipeline()
    finally:
        await close_db()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
