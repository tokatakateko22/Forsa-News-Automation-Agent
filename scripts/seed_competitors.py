"""
scripts/seed_competitors.py
────────────────────────────
Seeds the `competitors` table with the initial watchlist.
All 26 competitors across P1, P2, and Adjacent/Market Intelligence tiers.

Run once after database initialisation, or re-run to update existing entries.
Competitors can also be managed directly in the database without code changes.

Usage:
    py -3 scripts/seed_competitors.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.database.connection import get_session
from app.database.models import Competitor
from app.database.repositories import CompetitorRepository

# ── Competitor definitions ────────────────────────────────────────────────────
# priority: 1=direct BNPL/consumer-finance, 2=adjacent consumer-finance,
#           3=adjacent market intelligence (fintech, payments, banks)

COMPETITORS: list[dict] = [
    # ── Priority 1: Direct BNPL / Consumer-Finance Competitors ───────────────
    {
        "name": "U Consumer Finance / valU",
        "aliases": [
            "valU", "Valu", "U Consumer Finance", "U Finance",
            "فاليو", "يو للتمويل الاستهلاكي",
        ],
        "website": "https://valu.com.eg",
        "priority": 1,
    },
    {
        "name": "MNT-Halan",
        "aliases": [
            "Halan", "MNT Halan", "MNT-Halan",
            "حالا", "حالان", "حالا للتمويل الاستهلاكي",
        ],
        "website": "https://halan.com",
        "priority": 1,
    },
    {
        "name": "Contact Financial Holding",
        "aliases": [
            "Contact", "Contact Now", "Contact Credit",
            "Contact CrediTech", "كونتكت", "كونتكت ناو",
        ],
        "website": "https://contact.eg",
        "priority": 1,
    },
    {
        "name": "Aman Holding",
        "aliases": [
            "Aman", "Aman Finance", "Aman Consumer Finance",
            "أمان", "أمان للتمويل الاستهلاكي",
        ],
        "website": "https://aman.eg",
        "priority": 1,
    },
    {
        "name": "Souhoola",
        "aliases": [
            "Souhoola", "سهولة", "BM Consumer Finance", "B.M. Consumer Finance",
        ],
        "website": "https://souhoola.com",
        "priority": 1,
    },
    {
        "name": "Sympl",
        "aliases": ["Sympl", "سيمبل"],
        "website": "https://sympl.ai",
        "priority": 1,
    },
    {
        "name": "B.TECH Finance",
        "aliases": [
            "BTECH Finance", "B.TECH", "B.TECH Financial Services",
            "بي تك للتمويل", "BTECH",
        ],
        "website": "https://btech.com",
        "priority": 1,
    },
    {
        "name": "Premium Card",
        "aliases": [
            "Premium Card", "Premium International",
            "Premium International for Credit Services",
            "بريميوم كارد",
        ],
        "website": "https://premiumcard.net",
        "priority": 1,
    },
    {
        "name": "Shahry",
        "aliases": ["Shahry", "شهري"],
        "website": "https://shahry.com",
        "priority": 1,
    },
    {
        "name": "Blnk",
        "aliases": ["BLNK", "Blnk", "Blnk Consumer Finance", "بلنك"],
        "website": "https://blnk.ai",
        "priority": 1,
    },
    {
        "name": "Fawry",
        "aliases": [
            "Fawry", "Fawry Plus", "myFawry", "Fawry Consumer Finance",
            "فوري", "فوري بلس", "ماي فوري", "فوري للتمويل الاستهلاكي", "فوري يومي",
        ],
        "website": "https://fawry.com",
        "priority": 1,
    },
    # ── Priority 2: Adjacent Consumer-Finance Players ─────────────────────────
    {
        "name": "Seven / Beltone Consumer Finance",
        "aliases": [
            "Seven", "Seven Consumer Finance", "Beltone Consumer Finance",
            "Seven-Beltone", "سفن", "بلتون للتمويل الاستهلاكي",
        ],
        "website": "https://www.beltoneholding.com",
        "priority": 2,
    },
    {
        "name": "Jameel Finance Egypt",
        "aliases": [
            "Jameel Finance", "Abdul Latif Jameel Finance",
            "Jameel", "جميل للتمويل",
        ],
        "website": "https://www.jameel.com",
        "priority": 2,
    },
    {
        "name": "Rawaq Finance",
        "aliases": ["Rawaq", "Rawaq Consumer Finance", "رواج للتمويل الاستهلاكي"],
        "website": None,
        "priority": 2,
    },
    {
        "name": "Sky Finance",
        "aliases": [
            "Sky Finance", "Sky Finance Consumer Finance",
            "Sky Finance for Auto Installments", "سكاي فاينانس",
        ],
        "website": None,
        "priority": 2,
    },
    {
        "name": "Mogo Egypt",
        "aliases": ["Mogo", "Mogo Egypt"],
        "website": None,
        "priority": 2,
    },
    {
        "name": "TRU",
        "aliases": ["TRU", "Tru Finance"],
        "website": None,
        "priority": 2,
    },
    {
        "name": "Klivvr",
        "aliases": ["Klivvr", "كليفّر"],
        "website": "https://klivvr.com",
        "priority": 2,
    },
    {
        "name": "mylo",
        "aliases": ["mylo", "Mylo BNPL"],
        "website": None,
        "priority": 2,
    },
    {
        "name": "Takka Finance",
        "aliases": ["Takka", "Takka Finance"],
        "website": None,
        "priority": 2,
    },
    # ── Priority 3: Adjacent Market Intelligence (Fintech, Payments, Banks) ───
    {
        "name": "Paymob",
        "aliases": ["Paymob", "باي موب"],
        "website": "https://paymob.com",
        "priority": 3,
    },
    {
        "name": "Khazna",
        "aliases": ["Khazna", "خزنة", "Khazna Data Associates"],
        "website": "https://khazna.com",
        "priority": 3,
    },
    {
        "name": "Money Fellows",
        "aliases": ["Money Fellows", "موني فيلوز"],
        "website": "https://moneyfellows.com",
        "priority": 3,
    },
    {
        "name": "Bokra",
        "aliases": ["Bokra", "بكرة"],
        "website": "https://bokra.com",
        "priority": 3,
    },
    {
        "name": "Mashreq Egypt",
        "aliases": ["Mashreq Egypt", "مشرق مصر", "Mashreq Bank Egypt"],
        "website": "https://mashreq.com",
        "priority": 3,
    },
    {
        "name": "CIB Egypt",
        "aliases": [
            "CIB", "Commercial International Bank",
            "البنك التجاري الدولي",
        ],
        "website": "https://cib.eg",
        "priority": 3,
    },
    {
        "name": "Banque Misr",
        "aliases": ["Banque Misr", "بنك مصر"],
        "website": "https://banquemisr.com",
        "priority": 3,
    },
    {
        "name": "National Bank of Egypt",
        "aliases": ["NBE", "National Bank of Egypt", "البنك الأهلي المصري"],
        "website": "https://nbe.com.eg",
        "priority": 3,
    },
    {
        "name": "QNB Egypt",
        "aliases": ["QNB", "Qatar National Bank Egypt", "بنك قطر الوطني مصر"],
        "website": "https://qnb.com.eg",
        "priority": 3,
    },
    {
        "name": "AlexBank",
        "aliases": ["AlexBank", "البنك الإسكندرية", "Alexandria Bank"],
        "website": "https://alexbank.com",
        "priority": 3,
    },
    {
        "name": "FABMISR",
        "aliases": ["FABMISR", "First Abu Dhabi Bank Egypt", "فاب مصر", "FAB Egypt"],
        "website": "https://fabmisr.com",
        "priority": 3,
    },
]


async def seed_competitors() -> None:
    print(f"Seeding {len(COMPETITORS)} competitors...")
    async with get_session() as session:
        repo = CompetitorRepository(session)
        created = 0
        updated = 0
        for c in COMPETITORS:
            c_data = dict(c)
            c_data.setdefault("active", True)
            comp = Competitor(**c_data)
            existing = await repo.upsert(comp)
            if existing.id is None:
                created += 1
            else:
                updated += 1
    print(f"Done — seeded {len(COMPETITORS)} competitors (P1: 11, P2: 9, Adjacent: 11).")


if __name__ == "__main__":
    asyncio.run(seed_competitors())
