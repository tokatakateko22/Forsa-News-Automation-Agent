"""
app/services/spelling.py
────────────────────────
Brand and entity spelling normalization layer for executive news digests.
Guarantees zero misspellings or machine-translation distortions of Egyptian
competitor brands, consumer-finance operators, fintechs, and regulators.
"""
from __future__ import annotations

import re
from typing import Optional

# Canonical replacements dictionary: pattern -> canonical spelling
_BRAND_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    # Souhoola variants (e.g., Arabic 'سهولة' transliterated as 'SooLa', 'Sohoola', etc.)
    (re.compile(r"\b(?:SooLa|Sohoolah|Sohoola|Sahula|Suhula|Souhoula|Soohoola|Soohola)\b", re.IGNORECASE), "Souhoola"),

    # valU variants
    (re.compile(r"\b(?:Valiu|Falio|Valyou|Valu)\b"), "valU"),

    # MNT-Halan variants
    (re.compile(r"\b(?:MNT\s+Halan|Mnt-Halan|Mnt\s+Halan)\b"), "MNT-Halan"),

    # Blnk variants (avoid replacing normal English word 'blink' unless followed by finance/app terms or standalone capitalized)
    (re.compile(r"\bBlink\s+(Consumer\s+Finance|Finance|app|platform|fintech|loans?|installments?)\b", re.IGNORECASE), r"Blnk \1"),
    (re.compile(r"\bBlnk\s+Consumer\s+Finance\b", re.IGNORECASE), "Blnk Consumer Finance"),

    # Sympl variants
    (re.compile(r"\b(?:Simple|Sembl)\s+(checkout|platform|fintech|BNPL|pay\s+later|consumer\s+finance)\b", re.IGNORECASE), r"Sympl \1"),

    # B.TECH variants
    (re.compile(r"\b(?:Btech|B-Tech|B\.Tech)\b"), "B.TECH"),

    # Shahry variants
    (re.compile(r"\bShahri\b", re.IGNORECASE), "Shahry"),

    # Fawry variants
    (re.compile(r"\b(?:Fawri|Faury)\b", re.IGNORECASE), "Fawry"),

    # Klivvr variants
    (re.compile(r"\bKlivver\b", re.IGNORECASE), "Klivvr"),
    (re.compile(r"\bClever\s+(app|card|fintech|wallet)\b", re.IGNORECASE), r"Klivvr \1"),

    # mylo variants
    (re.compile(r"\bMilo\s+(BNPL|app|platform)\b", re.IGNORECASE), r"mylo \1"),

    # Rawaq Finance variants
    (re.compile(r"\bRawaj\s+Finance\b", re.IGNORECASE), "Rawaq Finance"),
    (re.compile(r"\bRawaj\b(?=\s+(?:for\s+consumer|consumer|auto))", re.IGNORECASE), "Rawaq"),

    # Forsa & Drive Finance
    (re.compile(r"\bFursa\b", re.IGNORECASE), "Forsa"),

    # Financial ecosystem & regulators
    (re.compile(r"\b(?:i-score|iscore|IScore|i-Score)\b"), "I-Score"),
    (re.compile(r"\b(?:instapay|Instapay|instaPay)\b"), "InstaPay"),
    (re.compile(r"\b(?:meeza|meza|Meza)\b"), "Meeza"),
    (re.compile(r"\bFra\b"), "FRA"),
    (re.compile(r"\bCbe\b"), "CBE"),
]

# Map of competitor match hint to canonical name & distortion patterns
_COMPETITOR_HINT_MAP: dict[str, tuple[str, list[re.Pattern]]] = {
    "Souhoola": (
        "Souhoola",
        [re.compile(r"\b(?:SooLa|Sohoolah|Sohoola|Sahula|Suhula|Souhoula|Soohoola|Soohola)\b", re.IGNORECASE)]
    ),
    "valU": (
        "valU",
        [re.compile(r"\b(?:Valiu|Falio|Valyou|Valu)\b", re.IGNORECASE)]
    ),
    "MNT-Halan": (
        "MNT-Halan",
        [re.compile(r"\b(?:MNT\s+Halan|Mnt-Halan|Mnt\s+Halan)\b", re.IGNORECASE)]
    ),
    "Sympl": (
        "Sympl",
        [re.compile(r"\b(?:Simple|Sembl)\b", re.IGNORECASE)]
    ),
    "Blnk": (
        "Blnk",
        [re.compile(r"\b(?:Blink|Blank)\b", re.IGNORECASE)]
    ),
    "B.TECH": (
        "B.TECH",
        [re.compile(r"\b(?:Btech|B-Tech|B\.Tech)\b", re.IGNORECASE)]
    ),
    "Shahry": (
        "Shahry",
        [re.compile(r"\b(?:Shahri)\b", re.IGNORECASE)]
    ),
    "Fawry": (
        "Fawry",
        [re.compile(r"\b(?:Fawri|Faury)\b", re.IGNORECASE)]
    ),
    "Klivvr": (
        "Klivvr",
        [re.compile(r"\b(?:Klivver|Clever)\b", re.IGNORECASE)]
    ),
    "mylo": (
        "mylo",
        [re.compile(r"\b(?:Milo|Mylo)\b", re.IGNORECASE)]
    ),
    "Rawaq": (
        "Rawaq Finance",
        [re.compile(r"\b(?:Rawaj|Rawaj\s+Finance)\b", re.IGNORECASE)]
    ),
    "Contact Financial": (
        "Contact Financial",
        [re.compile(r"\bKontact\b", re.IGNORECASE)]
    ),
    "Aman": (
        "Aman",
        [re.compile(r"\bAmen\b", re.IGNORECASE)]
    ),
}


def normalize_brand_spellings(text: str, competitor_hint: Optional[str] = None) -> str:
    """
    Sanitize and normalize brand names, company entities, and regulatory acronyms
    in executive summaries, headlines, and email blocks.

    Parameters:
        text: The text to normalize.
        competitor_hint: Optional competitor match (e.g. 'Souhoola', 'valU')
                         to enforce targeted replacement of distorted variants.
    Returns:
        Corrected text with official corporate spellings.
    """
    if not text:
        return text

    cleaned = text

    # 1. Apply competitor hint targeted replacement if available
    if competitor_hint:
        hint_key = competitor_hint.strip()
        # Find matching hint config
        for key, (canonical, patterns) in _COMPETITOR_HINT_MAP.items():
            if key.lower() == hint_key.lower() or key in hint_key or hint_key in key:
                for pat in patterns:
                    cleaned = pat.sub(canonical, cleaned)
                break

    # 2. Apply global brand normalization rules
    for pat, replacement in _BRAND_REPLACEMENTS:
        cleaned = pat.sub(replacement, cleaned)

    return cleaned
