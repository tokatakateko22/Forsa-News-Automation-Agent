"""
app/graph/nodes/preprocess.py
──────────────────────────────
Node 2: preprocess_news
Cleans raw article content before any LLM processing.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Optional

import chardet
import httpx
import structlog
from bs4 import BeautifulSoup

from app.graph.state import AgentState
from app.models.article import Article

log = structlog.get_logger(__name__)

# Common boilerplate phrases to strip
_BOILERPLATE_PATTERNS = [
    r"cookie policy.*",
    r"privacy policy.*",
    r"terms of (use|service).*",
    r"subscribe (now|to|for).*",
    r"sign (in|up) to.*",
    r"follow us on.*",
    r"share this article.*",
    r"read more.*",
    r"advertisement.*",
    r"sponsored content.*",
    r"\[?\.\.\.\]?",  # Truncation ellipsis markers
]

_BOILERPLATE_RE = re.compile(
    "|".join(_BOILERPLATE_PATTERNS),
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode HTML entities."""
    soup = BeautifulSoup(text, "lxml")
    # Remove script and style elements entirely
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "form", "button", "iframe"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces/newlines to single space."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _deduplicate_paragraphs(text: str) -> str:
    """Remove repeated sentences/paragraphs."""
    seen: set[str] = set()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    unique = []
    for s in sentences:
        normalized = s.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(s.strip())
    return " ".join(unique)


def _fix_encoding(text: str) -> str:
    """Fix common encoding issues."""
    # Normalize Unicode (NFC form)
    text = unicodedata.normalize("NFC", text)
    # Fix mojibake-style double-encoding
    try:
        text = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return text


def _remove_boilerplate(text: str) -> str:
    return _BOILERPLATE_RE.sub("", text).strip()


def clean_article_content(raw_content: Optional[str]) -> str:
    """Full preprocessing pipeline for article content."""
    if not raw_content:
        return ""

    text = _fix_encoding(raw_content)
    text = _strip_html(text)
    text = _remove_boilerplate(text)
    text = _deduplicate_paragraphs(text)
    text = _normalize_whitespace(text)
    return text


def clean_article(article: Article) -> Article:
    """Return a copy of the article with cleaned content and title."""
    clean_content = clean_article_content(article.content)
    clean_title = _normalize_whitespace(_strip_html(article.title))

    # Return updated article
    return article.model_copy(
        update={
            "title": clean_title,
            "content": clean_content,
        }
    )


_EXTRACTION_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


async def _fetch_full_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch article body paragraphs if raw content is a brief snippet or empty."""
    if not url.startswith("http"):
        return ""
    try:
        resp = await client.get(url, headers=_EXTRACTION_HEADERS)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "button", "iframe"]):
            tag.decompose()

        found_div = None
        for div in soup.find_all("div"):
            classes = div.get("class")
            if classes:
                classes_str = " ".join(classes) if isinstance(classes, list) else str(classes)
                if any(k in classes_str.lower() for k in ("content", "article", "entry", "post", "story", "detail")):
                    found_div = div
                    break

        article = (
            soup.find("article")
            or soup.find("main")
            or found_div
            or soup.body
        )
        if not article:
            return ""

        paragraphs = article.find_all("p") if article else []
        meaningful = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 25]
        if not meaningful or sum(len(m) for m in meaningful) < 100:
            doc_meaningful = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 25]
            if len(" ".join(doc_meaningful)) > len(" ".join(meaningful)):
                meaningful = doc_meaningful

        if meaningful:
            return " ".join(meaningful)[:4000]
        return ""
    except Exception:
        return ""


async def preprocess_news(state: AgentState) -> AgentState:
    """
    LangGraph node: preprocess_news
    Cleans HTML, normalises encoding, deduplicates paragraphs,
    and enriches articles with full body text when content is too brief.
    """
    raw_articles = state["raw_articles"]
    log.info(
        "node.preprocess.start",
        article_count=len(raw_articles),
    )

    clean_articles: list[Article] = []
    articles_needing_fetch: list[tuple[int, Article]] = []

    for article in raw_articles:
        try:
            cleaned = clean_article(article)
            # Skip articles with no useful title
            if len(cleaned.title) < 5:
                log.debug("preprocess.skip_empty", url=article.url)
                continue
            idx = len(clean_articles)
            clean_articles.append(cleaned)
            # If content is short, fetch full text only for articles in our monitoring scope or Tier 1
            if len(cleaned.content or "") < 200 and cleaned.url.startswith("http"):
                from app.graph.nodes.classify import _passes_keyword_filter
                if cleaned.source_tier == 1 or _passes_keyword_filter(cleaned):
                    articles_needing_fetch.append((idx, cleaned))
        except Exception as exc:
            log.warning(
                "preprocess.article_failed",
                url=article.url,
                error=str(exc),
            )

    # Concurrently enrich articles needing full text
    if articles_needing_fetch:
        log.info("preprocess.enriching_content", count=len(articles_needing_fetch))
        sem = asyncio.Semaphore(10)

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            async def _enrich(list_idx: int, art: Article) -> None:
                async with sem:
                    try:
                        full_text = await _fetch_full_text(client, art.url)
                        if full_text and len(full_text) > len(art.content or ""):
                            cleaned_body = clean_article_content(full_text)
                            if len(cleaned_body) > len(art.content or ""):
                                clean_articles[list_idx] = art.model_copy(
                                    update={"content": cleaned_body}
                                )
                    except Exception:
                        pass

            tasks = [_enrich(idx, art) for idx, art in articles_needing_fetch]
            await asyncio.gather(*tasks, return_exceptions=True)

    log.info(
        "node.preprocess.done",
        input=len(raw_articles),
        output=len(clean_articles),
    )

    stats = dict(state.get("stats", {}))
    stats["articles_preprocessed"] = len(clean_articles)

    return {
        **state,
        "clean_articles": clean_articles,
        "stats": stats,
    }
