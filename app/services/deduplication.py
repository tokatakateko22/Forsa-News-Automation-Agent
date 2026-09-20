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
import re
import uuid
from datetime import datetime, timedelta
from typing import Mapping, Optional

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


GENERIC_REGULATOR_ENTITIES = {
    "financial regulatory authority", "fra", "central bank of egypt", "cbe",
    "الرقابة المالية", "البنك المركزي", "البنك المركزي المصري", "هيئة الرقابة المالية",
    "the egyptian exchange", "egx", "البورصة المصرية",
}


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
    dt_a: Optional[datetime],
    dt_b: Optional[datetime],
    hours: int = 24,
) -> bool:
    """Check if two timestamps are within `hours` of each other."""
    if dt_a is None or dt_b is None:
        return True  # If we don't know, assume they could be the same event
    delta = abs((dt_a - dt_b).total_seconds())
    return delta <= hours * 3600


def _normalize_url(url: str) -> str:
    """Normalize URLs for deduplication (e.g., strip language subpaths and trailing slashes)."""
    if not url:
        return ""
    u = url.strip()
    # Strip protocol and www for comparison
    u = re.sub(r"^https?://(?:www\.)?", "", u, flags=re.IGNORECASE)
    # Strip /en/ from bilingual regulator domains so Arabic and English pages match
    u = re.sub(r"(fra\.gov\.eg|cbe\.org\.eg)/en/", r"\1/", u, flags=re.IGNORECASE)
    u = u.split("#")[0].split("?")[0].rstrip("/")
    return u.lower()



def _get_regulatory_topic(title: str) -> str:
    """Identify specific regulatory / macro topic to prevent cross-topic over-clustering."""
    t = title.lower()
    # Check CBE rate stories first so MPC meeting previews mentioning inflation aren't trapped in INFLATION
    if _is_cbe_rate_story(title):
        return "CBE_RATES"
    if any(k in t for k in ["اي سكور", "آي سكور", "i-score", "iscore", "ربط لحظي", "الربط اللحظي", "credit reporting", "الاستعلام الائتماني", "174", "175"]):
        return "ISCORE"
    if any(k in t for k in ["otp", "رمز التحقق", "رمز otp", "التحقق من هوية"]):
        return "OTP"
    if any(k in t for k in ["تقييم عقاري", "التقييم العقاري", "للتقييم العقاري", "معايير التقييم", "المعايير المصرية للتقييم", "real estate valuation", "191"]):
        return "VALUATION"
    if any(k in t for k in ["جلوبال بارادايم", "global paradigm", "أولين", "ollin", "319"]):
        return "OLLIN_SCANDAL"
    if any(k in t for k in ["دليل إشرافي", "دليل شامل", "قواعد التمويل الاستهلاكي", "supervisory manual", "عبء الدين"]):
        return "SUPERVISORY_MANUAL"
    if any(k in t for k in ["وثائق التأمين", "وثائق التامين", "تأمين على عملاء", "تامين على عملاء", "166"]):
        return "BORROWER_INSURANCE"
    if any(k in t for k in ["اقتراض الأوراق المالية", "short selling", "155"]):
        return "SHORT_SELLING"
    if any(k in t for k in ["تضخم", "cpi", "urban inflation", "core inflation", "التضخم"]):
        return "INFLATION"
    if any(k in t for k in ["احتياطي", "reserves", "foreign reserves", "international reserves", "الاحتياطي"]):
        return "RESERVES"
    if any(k in t for k in ["حالا", "halan"]) and any(k in t for k in ["قيد", "listing", "egx", "بورصة"]):
        return "HALAN_LISTING"
    if any(k in t for k in ["egx30", "egx 30", "البورصة المصرية"]) and any(k in t for k in ["أسبوع", "ختام", "flat week", "rally"]):
        return "MARKET_RECAP"
    return "OTHER"


def _is_cbe_rate_story(title: str) -> bool:
    t = title.lower()
    cbe_terms = ["مركزي", "المركزي", "cbe", "central bank"]
    rate_terms = [
        "فائدة", "الفائدة", "سعر الفائدة", "أسعار الفائدة",
        "interest rate", "interest rates", "rates", "rate", "mpc", "سياسة نقدية"
    ]
    meeting_terms = [
        "اجتماع", "تثبيت", "توقعات", "مصير", "يحسم", "حسم", "رفع", "خفض",
        "meeting", "hold", "cut", "hike", "decision", "decide", "سعر الفائدة"
    ]
    return any(w in t for w in cbe_terms) and any(w in t for w in rate_terms) and any(w in t for w in meeting_terms)



class DeduplicationService:
    """
    Stateless deduplication — takes a list of classified articles
    Multi-signal deduplication engine:
      1. Exact URL / content hash match (fastest)
      2. Time window check + Fuzzy title similarity (RapidFuzz token_sort_ratio)
      3. Topic clustering for recurring macro events (CBE MPC rate decisions)
      4. Named entity overlap + moderate title similarity
      5. Semantic embedding cosine similarity (fallback for rephrased titles)
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
        classifications: Mapping[str, Optional[ArticleClassification]],
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
            # URL dedup (with regulator language-subpath normalization)
            norm_url = _normalize_url(article.url)
            if norm_url and norm_url in url_map:
                union(article.article_id, url_map[norm_url])
            elif norm_url:
                url_map[norm_url] = article.article_id

            # Hash dedup
            if article.content_hash and article.content_hash in hash_map:
                union(article.article_id, hash_map[article.content_hash])
            elif article.content_hash:
                hash_map[article.content_hash] = article.article_id

        # Pass 2: Fuzzy title + entity + time matching
        article_list = list(articles)
        for i in range(len(article_list)):
            for j in range(i + 1, len(article_list)):
                a, b = article_list[i], article_list[j]
                if find(a.article_id) == find(b.article_id):
                    continue  # Already grouped

                cls_a = classifications.get(a.article_id)
                cls_b = classifications.get(b.article_id)

                topic_a = _get_regulatory_topic(a.title)
                topic_b = _get_regulatory_topic(b.title)

                # STRICT RULE 1: If both articles belong to recognized distinct topics, NEVER merge!
                if topic_a != "OTHER" and topic_b != "OTHER" and topic_a != topic_b:
                    continue

                same_event = False

                # 1. If both share the SAME specific recognized topic, merge into one canonical story
                if topic_a != "OTHER" and topic_a == topic_b:
                    same_event = True

                # 2. Competitor matching: same competitor + specific retail campaign / branch / cross-lingual concept
                if not same_event and cls_a and cls_b and cls_a.category == "Competitor" and cls_b.category == "Competitor":
                    if cls_a.competitor_match and cls_a.competitor_match == cls_b.competitor_match:
                        norm_a = _normalize_title(a.title).lower()
                        norm_b = _normalize_title(b.title).lower()
                        # Cross-lingual concept sets for competitor campaigns
                        competitor_concepts = [
                            {"customs", "جمارك", "ضرائب", "الجمارك", "الضرائب"},
                            {"arkan", "أركان", "اركان"},
                            {"iphone", "ايفون", "أيفون", "آيفون"},
                            {"orange", "أورنج", "اورنج", "اورانج"},
                            {"bupa", "بوبا"},
                        ]
                        for concept in competitor_concepts:
                            if any(k in norm_a for k in concept) and any(k in norm_b for k in concept):
                                same_event = True
                                break


                # For general deduplication, require 36h window
                if not same_event and not _within_time_window(a.published_at, b.published_at, hours=36):
                    continue

                # 3. High fuzzy title similarity (>= 80%)
                if not same_event and _titles_similar(a.title, b.title):
                    same_event = True

                # 4. Moderate title similarity (>= 68%) PLUS non-generic entity overlap
                if not same_event:
                    score = fuzz.token_sort_ratio(_normalize_title(a.title), _normalize_title(b.title))
                    if score >= 68:
                        if cls_a and cls_b and cls_a.entities and cls_b.entities:
                            specific_a = [e for e in cls_a.entities if e.lower() not in GENERIC_REGULATOR_ENTITIES]
                            specific_b = [e for e in cls_b.entities if e.lower() not in GENERIC_REGULATOR_ENTITIES]
                            if specific_a and specific_b and _entities_overlap(specific_a, specific_b):
                                same_event = True

                # 5. High semantic embedding similarity (must be >= 0.88, and neither has an isolated topic)
                if not same_event and self._embeddings and topic_a == "OTHER" and topic_b == "OTHER":
                    emb_a = self._embeddings.get(a.article_id)
                    emb_b = self._embeddings.get(b.article_id)
                    if emb_a and emb_b:
                        score = float(
                            cosine_similarity(
                                np.array(emb_a).reshape(1, -1),
                                np.array(emb_b).reshape(1, -1),
                            )[0][0]
                        )
                        threshold = max(0.88, settings.similarity_threshold)
                        if score >= threshold:
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
        classifications: Mapping[str, Optional[ArticleClassification]],
    ) -> NewsEvent:
        """
        Build a NewsEvent from a group of articles.
        Selects the canonical (best) article as the primary source:
        - Tier 1 sources preferred
        - Longest / most detailed content preferred among same tier
        - Most recent preferred as final tie-break
        """
        # Select canonical article:
        # Rule: Prioritize substantial body content (>= 80 chars) over empty stubs
        # 1. Substantial content preferred over empty stubs
        # 2. Prefer English articles over Arabic articles (0 for English, 1 for Arabic)
        # 3. Prefer Tier 1 official sources (FRA, CBE) among same language
        # 4. Longest content
        # 5. Most recent publication
        sorted_articles = sorted(
            articles,
            key=lambda a: (
                0 if (a.content and len(a.content.strip()) >= 80) else 1,
                0 if getattr(a, "is_english", False) else 1,
                a.source_tier,
                -len(a.content or ""),
                -(a.published_at.timestamp() if a.published_at else 0),
            ),
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
