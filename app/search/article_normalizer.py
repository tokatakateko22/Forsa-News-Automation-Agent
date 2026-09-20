"""
app/search/article_normalizer.py
────────────────────────────────
Normalizes articles discovered from any search/retrieval mechanism (SerpAPI, RSS,
direct website scraping) into the standard pipeline Article model.
Ensures uniform URL cleaning, date parsing, content hashing, and deduplication.
"""
from __future__ import annotations

import email.utils
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dateutil import parser as dparser

from app.models.article import Article

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "oc", "hl", "gl", "ceid",
}


def clean_url(raw_url: str) -> str:
    """Normalize URL by stripping tracking parameters, anchors, and redundant slashes."""
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url.strip())
        clean_params = [
            (k, v) for k, v in parse_qsl(parsed.query)
            if k.lower() not in _TRACKING_PARAMS
        ]
        # Sort query params for consistent canonicalization
        clean_params.sort(key=lambda x: x[0])
        new_query = urlencode(clean_params)
        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            new_query,
            "",  # Strip fragment/anchor
        ))
    except Exception:
        return raw_url.strip()


def parse_datetime(val: Any) -> Optional[datetime]:
    """Parse various date formats into a timezone-aware UTC datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)

    # If struct_time from feedparser
    if isinstance(val, time.struct_time):
        try:
            ts = time.mktime(val)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass

    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # Try email standard format (common in RSS feeds)
        try:
            parsed = email.utils.parsedate_to_datetime(val)
            if parsed:
                return parsed.astimezone(timezone.utc)
        except Exception:
            pass
        # Try dateutil fuzzy
        try:
            res = dparser.parse(val, fuzzy=True)
            if isinstance(res, tuple):
                res = res[0]
            if isinstance(res, datetime):
                if res.tzinfo is None:
                    return res.replace(tzinfo=timezone.utc)
                return res.astimezone(timezone.utc)
        except Exception:
            pass

    return None


def detect_language(title: str, content: Optional[str] = None) -> str:
    """Detect whether text is primarily Arabic or English."""
    combined = f"{title or ''} {content or ''}"
    if re.search(r"[\u0600-\u06FF]", combined):
        return "ar"
    return "en"


def make_normalized_article(
    *,
    title: str,
    url: str,
    source_name: str,
    source_tier: int = 2,
    source_id: Optional[int] = None,
    content: Optional[str] = None,
    published_at: Optional[Any] = None,
    language: Optional[str] = None,
) -> Optional[Article]:
    """Construct a clean, valid Article instance. Returns None if invalid."""
    t = (title or "").strip()
    u = clean_url(url)
    if not t or not u:
        return None

    # Strip HTML tags from title and content if any slipped through
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    c = (content or "").strip()
    if c:
        c = re.sub(r"<[^>]+>", " ", c)
        c = re.sub(r"\s+", " ", c).strip()

    dt = parse_datetime(published_at)
    lang = language or detect_language(t, c)

    return Article(
        title=t,
        url=u,
        source_name=source_name,
        source_tier=source_tier,
        source_id=source_id,
        content=c or None,
        published_at=dt,
        language=lang,
    )


def normalize_title_for_dedup(title: str) -> str:
    """Normalize title for fuzzy deduplication (removes publisher suffixes, punctuation, case)."""
    t = title.lower()
    # Strip common publisher suffixes like ' - Daily News Egypt', ' | Al Borsa', etc.
    t = re.sub(r"\s*[-|–—]\s*[^|–—]+$", "", t)
    # Remove punctuation
    t = re.sub(r"[^\w\s\u0600-\u06FF]", " ", t)
    # Collapse whitespace
    return re.sub(r"\s+", " ", t).strip()


def deduplicate_articles(articles: Sequence[Article]) -> list[Article]:
    """
    Deduplicate articles using canonical URL, content hash, and normalized title.
    Preserves highest priority/tier article when duplicates exist.
    """
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    seen_norm_titles: set[str] = set()
    unique: list[Article] = []

    for a in articles:
        if not a.url or not a.title:
            continue

        clean_u = clean_url(a.url)
        if clean_u in seen_urls:
            continue

        if a.content_hash and a.content_hash in seen_hashes:
            continue

        norm_title = normalize_title_for_dedup(a.title)
        if len(norm_title) > 20 and norm_title in seen_norm_titles:
            continue

        seen_urls.add(clean_u)
        if a.content_hash:
            seen_hashes.add(a.content_hash)
        if len(norm_title) > 20:
            seen_norm_titles.add(norm_title)

        unique.append(a)

    return unique
