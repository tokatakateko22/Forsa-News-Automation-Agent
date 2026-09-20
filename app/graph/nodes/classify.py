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
from typing import Optional

import structlog

from app.config import settings
from app.graph.state import AgentState
from app.models.article import Article, ArticleClassification
from app.services import llm

log = structlog.get_logger(__name__)

# ── Stage 1: Keyword pre-filter ───────────────────────────────────────────────
# If an article contains NONE of these terms, it's rejected without LLM call.

# ── Patterns for topics explicitly out of scope for a Consumer Finance CEO ──────
_EXCLUSION_RE = re.compile(
    r"(?:"
    r"got\s+talent|مسابقة|طلاب\s+الجامعات|جامعات|hackathon|"
    r"تجديد\s+تعيين|مساعد\s+رئيس\s+الهيئة|reappointed\s+assistant|assistant\s+to\s+chairman|"
    r"تأمين\s+الحريق|fire\s+insurance|تأمين\s+بحري|marine\s+insurance|مجمعة\s+التأمين|مجمعات\s+التأمين|insurance\s+pools?|"
    r"(?:طرح\s+|إصدار\s+|يطرح\s+)?(?:أذون|سندات)\s+خزانة|treasury\s+bills?|treasury\s+bonds?|t-bills?\s+auction|t-bonds?\s+auction|"
    r"الميسرات|ميسرات|الميسّرات"
    r")",
    re.IGNORECASE,
)

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

    # ── Competitors & Market Players (Compound brand terms to avoid false positives) ──
    "forsa", "فرصة",
    "valU", "valu", "u consumer finance", "فاليو", "يو للتمويل",
    "mnt-halan", "mnt halan", "حالان", "إم إن تي حالا", "إم ان تي حالا", "حالا للتمويل", "شركة حالا", "تطبيق حالا",
    "contact financial", "contact now", "contact credit", "كونتكت للتمويل", "كونتكت المالية", "شركة كونتكت",
    "aman finance", "aman holding", "أمان للتمويل", "شركة أمان", "أمان هولدنج", "أمان للتقسيط",
    "souhoola", "sohoola", "شركة سهولة", "سهولة للتمويل", "تطبيق سهولة",
    "sympl", "سيمبل للتمويل", "شركة سيمبل",
    "btech", "b.tech", "btech finance", "minicash", "بي تك للتمويل",
    "premium card", "بريميوم كارد",
    "shahry", "شهري للتمويل",
    "blnk", "بلنك للتمويل", "شركة بلنك",
    "fawry", "myfawry", "فوري للتمويل", "شركة فوري",
    "paymob", "باي موب",
    "khazna", "خزنة للتمويل",
    "money fellows", "مني فيلوز",
    "onefinance", "bedaya", "tamweel", "mashroey", "tasheel", "tanmeyah",

    "inflation", "cpi", "capmas", "gdp", "exchange rate", "devaluation", "fx",
    "egypt finance", "egyptian economy",
    "reserves", "foreign reserves", "net international reserves", "international reserves",
    "التضخم", "سعر الصرف", "الجنيه المصري", "احتياطي", "احتياطيات", "احتياطي النقد الأجنبي",
    "تحويلات المصريين",
    # ── Enforcement, Codification & Market Indicators ─────────────────────
    "جلوبال بارادايم", "global paradigm", "أولين", "ollin", "جلوبال كورب", "globalcorp",
    "دليل إشرافي", "الدليل الإشرافي", "supervisory manual", "عبء الدين",
    "egx30", "egx 30", "البورصة المصرية",
]

_KEYWORD_RE = re.compile(
    "|".join(re.escape(kw) for kw in SCOPE_KEYWORDS),
    re.IGNORECASE,
)


def _normalize_arabic(text: str) -> str:
    """Normalize Arabic characters (alefs, yehs, teh marbuta) and strip punctuation."""
    text = re.sub(r'[«»\"\'\(\)\[\]]', ' ', text)
    text = re.sub(r'[أإآ]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    text = re.sub(r'ة', 'ه', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()


def _passes_keyword_filter(article: Article) -> bool:
    """Fast deterministic check — does this article touch our scope at all?"""
    # Check exclusion pattern first - if title matches out of scope, drop immediately
    if _EXCLUSION_RE.search(article.title):
        return False

    # Tier 1 official sources (CBE, FRA)
    if article.source_tier == 1 or (article.source_name and any(
        s in article.source_name for s in ["Central Bank of Egypt", "Financial Regulatory Authority", "CBE", "FRA"]
    )):
        return True

    combined = f"{article.title} {article.content or ''}"
    norm = _normalize_arabic(combined)
    # Check competitor mentions in normalized text
    if any(k in norm for k in ["فاليو", "valu", "حالا", "halan", "سهول", "souhoola", "sohoola", "كونتكت", "contact", "سيمبل", "sympl", "بلنك", "blnk", "امان للتمويل", "شركه امان"]):
        return True

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


def _deterministic_classify(article: Article) -> Optional[ArticleClassification]:
    """Fast rule-based classification for high-confidence official sources and competitors."""
    src = article.source_name or ""
    title_and_content = f"{article.title} {article.content or ''}"
    norm_title = _normalize_arabic(article.title)
    norm_text = _normalize_arabic(title_and_content)

    # Drop any excluded topics that slipped through
    if _EXCLUSION_RE.search(article.title):
        return ArticleClassification(
            article_id=article.article_id,
            category="Other",
            is_relevant=False,
            importance_score=0,
            relevance_score=0,
            confidence=1.0,
        )

    # 1. Official Regulators (Tier 1)
    if any(k in src for k in ["Financial Regulatory Authority", "FRA", "الرقابة المالية"]):
        fra_consumer_finance_scope = [
            "تمويل استهلاكي", "تمويل الاستهلاك", "تمويل استهلاك", "تمويل الافراد", "تمويل أفراد",
            "consumer finance", "consumer credit", "retail lending",
            "تقسيط", "التقسيط", "الشراء الان والدفع لاحقا", "الشراء الآن والدفع لاحقاً", "bnpl", "buy now pay later",
            "اي سكور", "آي سكور", "i-score", "iscore", "استعلام ائتماني", "الربط اللحظي",
            "رمز التحقق", "otp", "التحقق من هويه العملاء", "التحقق من هوية العملاء", "e-kyc", "ekyc",
            "سقف عبء الدين", "عبء الدين", "debt burden",
            "تمويل المشروعات المتوسطه والصغيره", "تمويل المشروعات المتوسطة والصغيرة", "متناهي الصغر", "microfinance",
            "التحليل السلوكي", "behavioural analysis", "behavioral analysis", "credit scoring",
            "فاليو", "حالا", "كونتكت", "امان", "أمان", "سهوله", "سهولة", "سيمبل", "بلنك", "فرصه", "فرصة", "اولين", "أولين",
        ]
        if any(term in norm_text for term in fra_consumer_finance_scope):
            return ArticleClassification(
                article_id=article.article_id,
                category="FRA",
                subcategory="Consumer Finance Regulation",
                entities=["Financial Regulatory Authority"],
                is_relevant=True,
                importance_score=95,
                relevance_score=100,
                confidence=1.0,
            )
        else:
            return ArticleClassification(
                article_id=article.article_id,
                category="FRA",
                subcategory="Other Regulatory News",
                is_relevant=False,
                importance_score=20,
                relevance_score=20,
                confidence=0.9,
            )

    if any(k in src for k in ["Central Bank of Egypt", "CBE", "البنك المركزي"]) or article.source_tier == 1:
        cbe_in_scope = [
            "فائده", "الفائده", "سياسه نقديه", "mpc", "interest rate", "تضخم", "التضخم", "cpi",
            "احتياطي", "reserves", "سعر الصرف", "انستاباي", "instapay", "ميزه", "meeza",
            "محافظ", "تمويل", "ائتمان", "قروض"
        ]
        if any(term in norm_text for term in cbe_in_scope):
            return ArticleClassification(
                article_id=article.article_id,
                category="CBE",
                subcategory="Official Announcement",
                entities=["Central Bank of Egypt"],
                is_relevant=True,
                importance_score=95,
                relevance_score=100,
                confidence=1.0,
            )
        else:
            return ArticleClassification(
                article_id=article.article_id,
                category="CBE",
                is_relevant=False,
                importance_score=30,
                relevance_score=30,
                confidence=0.9,
            )

    # 2. FRA Decisions, Supervisory Manuals, and Enforcement Actions
    is_ollin_enforcement = (
        any(k in norm_text for k in ["جلوبال بارادايم", "global paradigm", "جلوبال كورب", "globalcorp", "شركة اولين", "شركة أولين", "اولين للتمويل", "أولين للتمويل"])
        or bool(re.search(r"\b(?:ollin|globalcorp)\b", article.title + " " + (article.content or ""), re.I))
    )
    if is_ollin_enforcement:
        return ArticleClassification(
            article_id=article.article_id,
            category="FRA",
            subcategory="Market Enforcement",
            entities=["Ollin Consumer Finance", "GlobalCorp", "Financial Regulatory Authority"],
            is_relevant=True,
            importance_score=95,
            relevance_score=100,
            confidence=1.0,
        )

    if any(k in norm_text for k in ["دليل شامل", "دليل اشرافي", "الدليل الاشرافي", "supervisory manual"]) and any(
        k in norm_text for k in ["تمويل استهلاكي", "consumer finance", "الرقابه الماليه", "fra"]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="FRA",
            subcategory="Supervisory Manual",
            entities=["Financial Regulatory Authority"],
            is_relevant=True,
            importance_score=95,
            relevance_score=100,
            confidence=1.0,
        )

    if any(k in norm_text for k in ["الرقابه الماليه", "fra"]) and any(
        k in norm_title for k in ["تمويل استهلاكي", "consumer finance", "اي سكور", "i-score", "credit reporting", "عبء الدين", "سقف عبء الدين", "رمز التحقق", "otp"]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="FRA",
            subcategory="Consumer Finance Regulation",
            entities=["Financial Regulatory Authority"],
            is_relevant=True,
            importance_score=95,
            relevance_score=100,
            confidence=1.0,
        )

    if any(k in norm_title for k in ["اي سكور", "i-score", "الربط اللحظي"]) and any(
        k in norm_text for k in ["تمويل", "استهلاك", "الرقابه الماليه", "قرار"]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="FRA",
            subcategory="Regulatory Decision",
            entities=["Financial Regulatory Authority", "I-Score"],
            is_relevant=True,
            importance_score=95,
            relevance_score=100,
            confidence=1.0,
        )

    # 3. Market Backdrop (EGX30 / Weekly Summary)
    if ("egx30" in norm_title or "egx 30" in norm_title or "البورصه المصريه" in norm_title) and any(
        k in norm_title for k in ["اسبوع", "ختام", "مكاسب", "خسائر", "flat week", "rally", "record"]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="Financial Market",
            subcategory="Market Recap",
            entities=["The Egyptian Exchange"],
            is_relevant=True,
            importance_score=75,
            relevance_score=85,
            confidence=0.95,
        )

    # 3. Key Competitors with Normalized Brand Matching
    # Souhoola
    if ("سهول" in norm_title or "souhoola" in norm_title or "sohoola" in norm_title) and any(
        w in norm_text for w in [
            "فرع", "تقسيط", "تمويل", "قسط", "حجز", "اركان", "ايفون", "iphone", "اورنج", "orange", "شراكه", "توسع",
            "installment", "finance", "financing", "branch", "partner", "credit", "bnpl"
        ]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="Competitor",
            subcategory="Market Activity",
            entities=["Souhoola"],
            is_relevant=True,
            importance_score=85,
            relevance_score=95,
            confidence=0.95,
            competitor_match="Souhoola",
        )

    # MNT-Halan
    if ("حالا" in norm_title or "halan" in norm_title) and any(
        w in norm_text for w in [
            "قيد", "بورصه", "تمويل", "تقسيط", "اسهم", "listing", "egx", "سندات", "توريق", "استثمار",
            "installment", "finance", "financing", "credit", "shares", "fintech"
        ]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="Competitor",
            subcategory="Market Activity",
            entities=["MNT-Halan"],
            is_relevant=True,
            importance_score=85,
            relevance_score=95,
            confidence=0.95,
            competitor_match="MNT-Halan",
        )

    # Valu
    if ("فاليو" in norm_title or "valu" in norm_title or "يو للتمويل" in norm_title) and any(
        w in norm_text for w in [
            "تقسيط", "تمويل", "رسوم", "جمارك", "شراء", "قسط", "bnpl", "شراكه", "تطبيق",
            "installment", "installments", "finance", "financing", "customs", "credit", "loan", "partnership"
        ]
    ):
        return ArticleClassification(
            article_id=article.article_id,
            category="Competitor",
            subcategory="Market Activity",
            entities=["valU"],
            is_relevant=True,
            importance_score=85,
            relevance_score=95,
            confidence=0.95,
            competitor_match="valU",
        )

    # Aman (strict: requires company compound name)
    if ("شركه امان" in norm_text or "امان للتمويل" in norm_text or "امان هولدنج" in norm_text or "امان للتقسيط" in norm_text or "تطبيق امان" in norm_text or "aman finance" in norm_text or "aman holding" in norm_text):
        if any(w in norm_text for w in ["تقسيط", "تمويل", "قسط", "شراء", "تطبيق", "فرع", "installment", "finance", "financing", "credit", "branch"]):
            return ArticleClassification(
                article_id=article.article_id,
                category="Competitor",
                subcategory="Market Activity",
                entities=["Aman"],
                is_relevant=True,
                importance_score=85,
                relevance_score=95,
                confidence=0.95,
                competitor_match="Aman",
            )

    # Contact Financial
    if ("كونتكت" in norm_title or "contact financial" in norm_title or "contact now" in norm_title or "contact credit" in norm_title):
        return ArticleClassification(
            article_id=article.article_id,
            category="Competitor",
            subcategory="Market Activity",
            entities=["Contact Financial"],
            is_relevant=True,
            importance_score=85,
            relevance_score=95,
            confidence=0.95,
            competitor_match="Contact Financial",
        )

    # Sympl
    if ("سيمبل" in norm_title or "sympl" in norm_title):
        return ArticleClassification(
            article_id=article.article_id,
            category="Competitor",
            subcategory="Market Activity",
            entities=["Sympl"],
            is_relevant=True,
            importance_score=85,
            relevance_score=95,
            confidence=0.95,
            competitor_match="Sympl",
        )

    # Blnk
    if ("بلنك" in norm_title or "blnk" in norm_title):
        return ArticleClassification(
            article_id=article.article_id,
            category="Competitor",
            subcategory="Market Activity",
            entities=["Blnk"],
            is_relevant=True,
            importance_score=85,
            relevance_score=95,
            confidence=0.95,
            competitor_match="Blnk",
        )

    return None


async def classify_articles(state: AgentState) -> AgentState:
    """
    LangGraph node: classify_articles
    Stage 1: keyword pre-filter (deterministic, free)
    Stage 2: Deterministic classification for Regulators & Competitors (zero LLM calls)
    Stage 3: Gemini Flash LLM classification for candidate subset (capped to protect API quota)
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

    classifications: dict[str, ArticleClassification] = {}
    remaining_candidates: list[Article] = []

    # Stage 2: Deterministic classification
    for a in candidates:
        det_cls = _deterministic_classify(a)
        if det_cls:
            classifications[a.article_id] = det_cls
        else:
            remaining_candidates.append(a)

    log.info(
        "node.classify.deterministic",
        deterministic_count=len(classifications),
        remaining_for_llm=len(remaining_candidates),
    )

    # Stage 3: LLM classification for remaining candidates (cap at 15 to stay within free tier RPM)
    MAX_LLM_CLASSIFY = 15
    llm_batch = remaining_candidates[:MAX_LLM_CLASSIFY]

    for a in llm_batch:
        cls = await _classify_one(a)
        classifications[a.article_id] = cls

    # Build final list of relevant articles
    relevant_articles: list[Article] = []
    for article in candidates:
        cls = classifications.get(article.article_id)
        if cls and cls.is_relevant and cls.relevance_score >= settings.relevance_threshold:
            relevant_articles.append(article)

    log.info(
        "node.classify.done",
        total_candidates=len(candidates),
        classified=len(classifications),
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
