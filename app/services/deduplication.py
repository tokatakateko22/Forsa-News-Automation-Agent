"""
app/services/deduplication.py
──────────────────────────────
Multi-signal deduplication service.
Groups articles that cover the same underlying event into NewsEvent objects.

Signal stack (applied in order, most deterministic first):
  1. Exact URL match         — same article, instant group
  2. Content hash match      — same content, different URL
  3. Fuzzy title similarity  — >TITLE_SIMILARITY_THRESHOLD
  4. Entity + time overlap   — same entities mentioned within 24h
  5. Semantic embedding cos  — >SIMILARITY_THRESHOLD (most expensive, last)
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Optional

import numpy as np
import structlog
from sklearn.metrics.pairwise import cosine_similarity
from thefuzz import fuzz

from app.config import settings
from app.models.article import Article, ArticleClassification
from app.models.event import NewsEvent

log = structlog.get_logger(__name__)


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation for comparison."""
    import re
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _titles_similar(a: str, b: str) -> bool:
    """Check if two titles are similar enough to be the same event."""
    score = fuzz.token_sort_ratio(_normalize_title(a), _normalize_title(b))
    return score >= settings.title_similarity_threshold


def _entities_overlap(entities_a: list[str], entities_b: list[str]) -> bool:
    """Check if two entity lists have significant overlap."""
    if not entities_a or not entities_b:
        return False
    set_a = {e.lower() for e in entities_a}
    set_b = {e.lower() for e in entities_b}
    overlap = len(set_a & set_b)
    min_size = min(len(set_a), len(set_b))
    return overlap >= max(1, min_size // 2)


def _within_time_window(
    dt_a: Optional[object],
    dt_b: Optional[object],
    hours: int = 24,
) -> bool:
    """Check if two timestamps are within `hours` of each other."""
    if dt_a is None or dt_b is None:
        return True  # If we don't know, assume they could be the same event
    delta = abs((dt_a - dt_b).total_seconds())
    return delta <= hours * 3600


class DeduplicationService:
    """
    Stateless deduplication — takes a list of classified articles
    and returns a list of NewsEvent objects.
    """

    def __init__(self, embeddings: Optional[dict[str, list[float]]] = None) -> None:
        """
        embeddings: optional pre-computed {article_id: embedding_vector} dict.
        If provided, semantic similarity is used as a final dedup signal.
        """
        self._embeddings = embeddings or {}

    def deduplicate(
        self,
        articles: list[Article],
        classifications: dict[str, ArticleClassification],
    ) -> list[NewsEvent]:
        """
        Group articles into events.
        Returns one NewsEvent per unique underlying story.
        """
        if not articles:
            return []

        # Union-Find (Disjoint Set Union) for efficient grouping
        parent: dict[str, str] = {a.article_id: a.article_id for a in articles}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            parent[find(x)] = find(y)

        url_map: dict[str, str] = {}      # url -> article_id
        hash_map: dict[str, str] = {}     # content_hash -> article_id

        # Pass 1: Exact URL + hash matching (O(n))
        for article in articles:
            # URL dedup
            if article.url in url_map:
                union(article.article_id, url_map[article.url])
            else:
                url_map[article.url] = article.article_id

            # Hash dedup
            if article.content_hash and article.content_hash in hash_map:
                union(article.article_id, hash_map[article.content_hash])
            elif article.content_hash:
                hash_map[article.content_hash] = article.article_id

        # Pass 2: Fuzzy title + entity + time matching (O(n²) — acceptable for daily batches)
        article_list = list(articles)
        for i in range(len(article_list)):
            for j in range(i + 1, len(article_list)):
                a, b = article_list[i], article_list[j]
                if find(a.article_id) == find(b.article_id):
                    continue  # Already grouped

                if not _within_time_window(a.published_at, b.published_at, hours=36):
                    continue

                same_event = False

                # Fuzzy title
                if _titles_similar(a.title, b.title):
                    same_event = True

                # Entity overlap (only if both have classifications)
                if not same_event:
                    cls_a = classifications.get(a.article_id)
                    cls_b = classifications.get(b.article_id)
                    if cls_a and cls_b and cls_a.entities and cls_b.entities:
                        if _entities_overlap(cls_a.entities, cls_b.entities):
                            same_event = True

                # Semantic embedding similarity (if embeddings available)
                if not same_event and self._embeddings:
                    emb_a = self._embeddings.get(a.article_id)
                    emb_b = self._embeddings.get(b.article_id)
                    if emb_a and emb_b:
                        score = float(
                            cosine_similarity(
                                np.array(emb_a).reshape(1, -1),
                                np.array(emb_b).reshape(1, -1),
                            )[0][0]
                        )
                        if score >= settings.similarity_threshold:
                            same_event = True

                if same_event:
                    union(a.article_id, b.article_id)

        # Build groups
        groups: dict[str, list[Article]] = {}
        for article in articles:
            root = find(article.article_id)
            groups.setdefault(root, []).append(article)

        # Create NewsEvent per group
        events: list[NewsEvent] = []
        for root_id, group_articles in groups.items():
            event = self._build_event(group_articles, classifications)
            events.append(event)

        log.info(
            "dedup.complete",
            input_articles=len(articles),
            output_events=len(events),
        )
        return events

    def _build_event(
        self,
        articles: list[Article],
        classifications: dict[str, ArticleClassification],
    ) -> NewsEvent:
        """
        Build a NewsEvent from a group of articles.
        Selects the canonical (best) article as the primary source:
        - Tier 1 sources preferred
        - Among same tier, most recent article preferred
        """
        # Sort: lower tier number = higher quality
        sorted_articles = sorted(
            articles,
            key=lambda a: (a.source_tier, -(a.published_at.timestamp() if a.published_at else 0)),
        )
        canonical = sorted_articles[0]
        cls = classifications.get(canonical.article_id)

        return NewsEvent(
            event_id=str(uuid.uuid4()),
            canonical_title=canonical.title,
            event_date=canonical.published_at,
            category=cls.category if cls else "Other",
            subcategory=cls.subcategory if cls else None,
            canonical_url=canonical.url,
            canonical_source_name=canonical.source_name,
            canonical_source_tier=canonical.source_tier,
            articles=articles,
            classification=cls,
            verification_status="UNVERIFIED",
            importance_score=cls.importance_score if cls else 0,
            relevance_score=cls.relevance_score if cls else 0,
        )
