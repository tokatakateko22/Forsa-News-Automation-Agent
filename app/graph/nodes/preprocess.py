"""
app/graph/nodes/preprocess.py
──────────────────────────────
Node 2: preprocess_news
Cleans raw article content before any LLM processing.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

import chardet
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


async def preprocess_news(state: AgentState) -> AgentState:
    """
    LangGraph node: preprocess_news
    Cleans HTML, normalises encoding, deduplicates paragraphs.
    """
    log.info(
        "node.preprocess.start",
        article_count=len(state["raw_articles"]),
    )

    clean_articles: list[Article] = []
    for article in state["raw_articles"]:
        try:
            cleaned = clean_article(article)
            # Skip articles with no useful content after cleaning
            if len(cleaned.title) < 5:
                log.debug("preprocess.skip_empty", url=article.url)
                continue
            clean_articles.append(cleaned)
        except Exception as exc:
            log.warning(
                "preprocess.article_failed",
                url=article.url,
                error=str(exc),
            )

    log.info(
        "node.preprocess.done",
        input=len(state["raw_articles"]),
        output=len(clean_articles),
    )

    stats = dict(state.get("stats", {}))
    stats["articles_preprocessed"] = len(clean_articles)

    return {
        **state,
        "clean_articles": clean_articles,
        "stats": stats,
    }
