"""
app/graph/nodes/classify.py
────────────────────────────
Node 3: classify_articles
Staged classification:
  Stage 1 — Deterministic keyword pre-filter (zero LLM cost)
  Stage 2 — Gemini Flash LLM classification for candidates
"""
from __future__ import annotations

import asyncio
import re

import structlog

from app.config import settings
from app.graph.state import AgentState
from app.models.article import Article, ArticleClassification
from app.services import llm

log = structlog.get_logger(__name__)

# ── Stage 1: Keyword pre-filter ───────────────────────────────────────────────
# If an article contains NONE of these terms, it's rejected without LLM call.

SCOPE_KEYWORDS = [
    # ── Regulators & Monetary Policy (CBE & FRA) ───────────────────────────
    "central bank", "cbe", "monetary policy", "interest rate", "interest rates",
    "corridor", "mpc", "discount rate", "cash reserve ratio",
    "financial regulatory", "financial regulatory authority", "fra",
    "non-bank", "nbfi", "nbfs", "capital market", "securitization", "securitisation",
    "البنك المركزي", "المركزي المصري", "الرقابة المالية", "لجنة السياسة النقدية",
    "سعر الفائدة", "أسعار الفائدة", "الأنشطة المالية غير المصرفية", "توريق", "سندات توريق",

    # ── Consumer Finance & Lending Dynamics ────────────────────────────────
    "consumer finance", "consumer credit", "retail lending", "retail banking",
    "bnpl", "buy now pay later", "installment", "instalment", "installments",
    "digital lending", "microfinance", "sme finance", "merchant financing",
    "credit limit", "debt recovery", "purchasing power", "consumer spending",
    "i-score", "iscore", "credit score", "credit reporting", "credit bureau",
    "تمويل استهلاكي", "تمويل أفراد", "تقسيط", "التقسيط", "الشراء الآن والدفع لاحقاً",
    "استعلام ائتماني", "اي سكور", "آي سكور", "القوة الشرائية", "تمويل متناهي الصغر",

    # ── FinTech, Payments & Infrastructure ─────────────────────────────────
    "fintech", "financial technology", "digital onboarding", "e-kyc", "ekyc",
    "instapay", "meeza", "mobile wallet", "digital wallet", "open banking",
    "تكنولوجيا مالية", "انستاباي", "ميزة", "محافظ إلكترونية",

    # ── Competitors & Market Players (Consumer Finance / BNPL) ─────────────
    "forsa", "فرصة",
    "valU", "valu", "u consumer finance", "فاليو",
    "halan", "mnt-halan", "mnt halan", "حالا", "حالان",
    "contact financial", "contact now", "contact credit", "كونتكت",
    "aman", "aman finance", "أمان",
    "souhoola", "سهولة",
    "sympl", "سيمبل",
    "btech", "b.tech", "btech finance", "minicash", "بي تك",
    "premium card", "بريميوم كارد",
    "shahry", "شهري",
    "blnk", "بلنك",
    "fawry", "myfawry", "فوري",
    "paymob", "باي موب",
    "khazna", "خزنة",
    "money fellows", "مني فيلوز",
    "onefinance", "bedaya", "tamweel", "mashroey", "tasheel", "tanmeyah",

    # ── Macroeconomic Drivers ──────────────────────────────────────────────
    "inflation", "cpi", "capmas", "gdp", "exchange rate", "devaluation", "fx",
    "egypt finance", "egyptian economy", "egyptian bank", "cairo bank",
    "التضخم", "سعر الصرف", "الجنيه المصري",
]

_KEYWORD_RE = re.compile(
    "|".join(re.escape(kw) for kw in SCOPE_KEYWORDS),
    re.IGNORECASE,
)


def _passes_keyword_filter(article: Article) -> bool:
    """Fast deterministic check — does this article touch our scope at all?"""
    combined = f"{article.title} {article.content or ''}"
    return bool(_KEYWORD_RE.search(combined))


# ── Stage 2: LLM classification ───────────────────────────────────────────────

_SEMAPHORE = asyncio.Semaphore(1)  # Sequential to respect API rate limits


async def _classify_one(article: Article) -> ArticleClassification:
    """Classify a single article with the LLM. Sequential pacing for Gemini."""
    await asyncio.sleep(4.0)  # Safe pacing between API calls
    try:
        result = await llm.classify_article(
            title=article.title,
            content=article.content or "",
        )
        return ArticleClassification(
            article_id=article.article_id,
            category=result.get("category", "Other"),
            subcategory=result.get("subcategory"),
            entities=result.get("entities", []),
            is_relevant=result.get("is_relevant", False),
            importance_score=int(result.get("importance_score", 0)),
            relevance_score=int(result.get("confidence", 0) * 100),
            confidence=result.get("confidence", 0.0),
            competitor_match=result.get("competitor_match"),
        )
    except Exception as exc:
        log.warning(
            "classify.llm_error",
            article_id=article.article_id,
            error=str(exc),
        )
        return ArticleClassification(
            article_id=article.article_id,
            is_relevant=False,
            category="Other",
        )


async def classify_articles(state: AgentState) -> AgentState:
    """
    LangGraph node: classify_articles
    Stage 1: keyword pre-filter (deterministic, free)
    Stage 2: LLM classification (Gemini Flash, sequential pacing)
    """
    articles = state["clean_articles"]
    log.info("node.classify.start", count=len(articles))

    # Stage 1: keyword filter
    candidates = [a for a in articles if _passes_keyword_filter(a)]
    rejected_keyword = len(articles) - len(candidates)
    log.info(
        "node.classify.keyword_filter",
        candidates=len(candidates),
        rejected=rejected_keyword,
    )

    # Stage 2: LLM classification (sequential to prevent concurrent bursts)
    raw_classifications: list[ArticleClassification] = []
    for a in candidates:
        cls = await _classify_one(a)
        raw_classifications.append(cls)

    classifications: dict[str, ArticleClassification] = {}
    relevant_articles: list[Article] = []

    for article, cls in zip(candidates, raw_classifications):
        classifications[article.article_id] = cls
        if cls.is_relevant and cls.relevance_score >= settings.relevance_threshold:
            relevant_articles.append(article)

    log.info(
        "node.classify.done",
        classified=len(candidates),
        relevant=len(relevant_articles),
    )

    stats = dict(state.get("stats", {}))
    stats["articles_relevant"] = len(relevant_articles)

    return {
        **state,
        "classifications": classifications,
        "relevant_articles": relevant_articles,
        "stats": stats,
    }
