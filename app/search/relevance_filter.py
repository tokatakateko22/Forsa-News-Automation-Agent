"""
app/search/relevance_filter.py
──────────────────────────────
High-precision relevance and date filtering layer for fallback retrieval (RSS & web scraping).
Ensures articles retrieved without SerpAPI meet the exact same thematic relevance criteria:
  1. Consumer Finance / BNPL / Installment Lending
  2. Forsa & Drive Finance
  3. Competitor Activities & Offerings
  4. Regulatory & Monetary Decisions (CBE & FRA)
  5. FinTech & Digital Financial Services
Filters out general news, sports, entertainment, and explicit executive exclusions.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Sequence

import structlog

from app.models.article import Article

log = structlog.get_logger(__name__)

# ── Explicit exclusions (irrelevancies for an executive digest) ───────────────
_EXCLUSION_RE = re.compile(
    r"(?:"
    r"got\s+talent|مسابقة|طلاب\s+الجامعات|جامعات|hackathon|"
    r"تجديد\s+تعيين|مساعد\s+رئيس\s+الهيئة|reappointed\s+assistant|assistant\s+to\s+chairman|"
    r"تأمين\s+الحريق|fire\s+insurance|تأمين\s+بحري|marine\s+insurance|مجمعة\s+التأمين|مجمعات\s+التأمين|insurance\s+pools?|"
    r"(?:طرح\s+|إصدار\s+|يطرح\s+)?(?:أذون|سندات)\s+خزانة|treasury\s+bills?|treasury\s+bonds?|t-bills?\s+auction|t-bonds?\s+auction|"
    r"كرة\s+القدم|مباراة|دوري|أهداف|football|championship|entertainment|cinema|فيلم|مسلسل|"
    r"الميسرات|ميسرات|الميسّرات"
    r")",
    re.IGNORECASE,
)

# ── 1. Consumer Finance Topics ────────────────────────────────────────────────
_CONSUMER_FINANCE_PATTERNS = [
    r"\bconsumer\s+finance\b", r"\bconsumer\s+credit\b", r"\bretail\s+lending\b",
    r"\bpersonal\s+loans?\b", r"\binstallment\b", r"\binstalment\b", r"\binstallments\b",
    r"\bbnpl\b", r"\bbuy\s+now\s+pay\s+later\b", r"\bretail\s+financing\b",
    r"\bcredit\s+products?\b", r"\bdigital\s+lending\b", r"\bcredit\s+scoring?\b",
    r"\bcredit\s+risk\b", r"\bcredit\s+bureau\b", r"\bcredit\s+reporting\b",
    r"\bi-score\b", r"\biscore\b", r"\bsecuritization\b", r"\bsecuritisation\b",
    r"\bfinancial\s+inclusion\b", r"\bpurchasing\s+power\b", r"\bdebt\s+burden\b",
    r"تمويل\s+استهلاكي", r"تمويل\s+الاستهلاك", r"تمويل\s+أفراد", r"تمويل\s+الأفراد",
    r"تقسيط", r"التقسيط", r"شراء\s+الآن\s+وادفع\s+لاحق", r"قروض\s+شخصية",
    r"استعلام\s+ائتماني", r"آي\s+سكور", r"اي\s+سكور", r"توريق", r"سندات\s+توريق",
    r"الشمول\s+المالي", r"القوة\s+الشرائية", r"عبء\s+الدين",
    r"عروض\s+(?:تقسيط|تمويل|شراء|كاش\s*باك)", r"بدون\s+فوائد", r"بدون\s+مقدم",
    r"كاش\s*باك", r"\bcashback\b", r"\bzero\s+interest\b", r"\bno\s+down\s+payment\b",
]

# ── 2. Forsa & Drive Finance ──────────────────────────────────────────────────
_FORSA_PATTERNS = [
    r"\bforsa\b", r"\bforsa\s+finance\b", r"\bforsa\s+app\b", r"\bforsa\s+consumer\b",
    r"\bdrive\s+finance\b", r"فرصة\s+للتمويل", r"تطبيق\s+فرصة", r"شركة\s+فرصة",
    r"درايف\s+فاينانس", r"درايف\s+للتمويل",
]

# ── 3. Competitors (Compound brand terms to avoid false positives) ─────────────
_COMPETITOR_PATTERNS = [
    r"\bvalu\b", r"\bu\s+consumer\s+finance\b", r"\bu\s+finance\b", r"فاليو", r"يو\s+للتمويل",
    r"\bmnt-halan\b", r"\bmnt\s+halan\b", r"حالان", r"إم\s+إن\s+تي\s+حالا", r"حالا\s+للتمويل",
    r"شركة\s+حالا", r"تطبيق\s+حالا",
    r"\bcontact\s+financial\b", r"\bcontact\s+now\b", r"\bcontact\s+credit\b",
    r"كونتكت\s+للتمويل", r"كونتكت\s+المالية", r"شركة\s+كونتكت",
    r"\baman\s+finance\b", r"\baman\s+holding\b", r"\baman\s+consumer\b",
    r"أمان\s+للتمويل", r"شركة\s+أمان", r"أمان\s+هولدنج", r"أمان\s+للتقسيط",
    r"\bsouhoola\b", r"\bsohoola\b", r"شركة\s+سهولة", r"سهولة\s+للتمويل", r"تطبيق\s+سهولة",
    r"\bsympl\b", r"سيمبل\s+للتمويل", r"شركة\s+سيمبل",
    r"\bbtech\b", r"\bb\.tech\b", r"\bbtech\s+finance\b", r"\bminicash\b", r"بي\s+تك\s+للتمويل",
    r"\bpremium\s+card\b", r"بريميوم\s+كارد",
    r"\bshahry\b", r"شهري\s+للتمويل",
    r"\bblnk\b", r"بلنك\s+للتمويل", r"شركة\s+بلنك",
    r"\bseven\b(?:\s+consumer|\s+beltone|\s+finance)", r"بلتون\s+للتمويل\s+الاستهلاكي", r"سفن\s+للتمويل",
    r"\bjameel\s+finance\b", r"جميل\s+للتمويل",
    r"\brawaq\s+finance\b", r"رواج\s+للتمويل",
    r"\bsky\s+finance\b", r"سكاي\s+فاينانس",
    r"\bmogo\b(?:\s+egypt|\s+finance)", r"\bklivvr\b", r"كليفّر",
    r"\bmylo\b(?:\s+bnpl|\s+finance)", r"\btakka\b(?:\s+finance)",
    r"\bfawry\b", r"\bmyfawry\b", r"\bfawry\s+plus\b",
    r"فوري\s+(?:للتمويل|بلس|بلاس|يومي|كاش|المالية)", r"شركة\s+فوري", r"تطبيق\s+(?:فوري|ماي\s*فوري)", r"ماي\s*فوري",
    r"\bpaymob\b", r"باي\s+موب",
    r"\bkhazna\b", r"خزنة\s+للتمويل",
    r"\bmoney\s+fellows\b", r"موني\s+فيلوز",
]

# ── 4. Regulatory & Monetary (CBE & FRA) ──────────────────────────────────────
_REGULATORY_PATTERNS = [
    r"\bcentral\s+bank\s+of\s+egypt\b", r"\bcbe\b", r"\bmonetary\s+policy\b",
    r"\binterest\s+rates?\b", r"\bcorridor\b", r"\bmpc\b", r"\bcash\s+reserve\b",
    r"\bfinancial\s+regulatory\s+authority\b", r"\bfra\b", r"\bnbfi\b", r"\bnbfs\b",
    r"\bsupervisory\s+manual\b",
    r"البنك\s+المركزي", r"المركزي\s+المصري", r"الرقابة\s+المالية", r"الهيئة\s+العامة\s+للرقابة\s+المالية",
    r"لجنة\s+السياسة\s+النقدية", r"سعر\s+الفائدة", r"أسعار\s+الفائدة",
    r"الأنشطة\s+المالية\s+غير\s+المصرفية", r"القطاع\s+المالي\s+غير\s+المصرفي",
    r"دليل\s+إشرافي", r"قواعد\s+التمويل\s+الاستهلاكي",
]

# ── 5. FinTech & Digital Financial Services ───────────────────────────────────
_FINTECH_PATTERNS = [
    r"\bfintech\b", r"\bfinancial\s+technology\b", r"\bdigital\s+onboarding\b",
    r"\be-kyc\b", r"\bekyc\b", r"\binstapay\b", r"\bmeeza\b",
    r"\bmobile\s+wallets?\b", r"\bdigital\s+wallets?\b", r"\bopen\s+banking\b",
    r"\bembedded\s+finance\b", r"\balternative\s+lending\b",
    r"تكنولوجيا\s+مالية", r"انستاباي", r"ميزة", r"محافظ\s+إلكترونية",
    r"محفظة\s+إلكترونية", r"مدفوعات\s+رقمية",
]

# Compile regex patterns for fast evaluation
_COMPILED_TOPICS = {
    "consumer_finance": [re.compile(p, re.IGNORECASE) for p in _CONSUMER_FINANCE_PATTERNS],
    "forsa": [re.compile(p, re.IGNORECASE) for p in _FORSA_PATTERNS],
    "competitor": [re.compile(p, re.IGNORECASE) for p in _COMPETITOR_PATTERNS],
    "regulatory": [re.compile(p, re.IGNORECASE) for p in _REGULATORY_PATTERNS],
    "fintech": [re.compile(p, re.IGNORECASE) for p in _FINTECH_PATTERNS],
}


def evaluate_article_relevance(
    article: Article,
    competitor_names: Optional[Sequence[str]] = None,
) -> tuple[bool, list[str]]:
    """
    Check if an article is relevant to Forsa news monitoring topics.
    Returns (is_relevant, matched_topics).
    """
    text = f"{article.title or ''} {article.content or ''}"
    if not text.strip():
        return False, []

    # Check exclusions first
    if _EXCLUSION_RE.search(text):
        return False, []

    matched_topics: list[str] = []

    for topic, patterns in _COMPILED_TOPICS.items():
        for pat in patterns:
            if pat.search(text):
                matched_topics.append(topic)
                break

    # Dynamic competitor aliases check if supplied
    if competitor_names and "competitor" not in matched_topics:
        for cname in competitor_names:
            if cname and len(cname) > 3:
                # Word boundary match
                pat = re.compile(rf"\b{re.escape(cname)}\b", re.IGNORECASE)
                if pat.search(text):
                    matched_topics.append("competitor")
                    break

    is_relevant = len(matched_topics) > 0
    return is_relevant, matched_topics


def filter_fallback_articles(
    articles: Sequence[Article],
    start_time: datetime,
    end_time: datetime,
    competitor_names: Optional[Sequence[str]] = None,
) -> list[Article]:
    """
    Apply date window and relevance filtering to a list of candidate articles.
    Logs matching statistics clearly.
    """
    in_window: list[Article] = []
    out_of_window_count = 0

    for a in articles:
        if a.published_at is not None:
            if a.published_at < start_time or a.published_at > end_time:
                out_of_window_count += 1
                continue
        in_window.append(a)

    relevant_articles: list[Article] = []
    excluded_count = 0

    for a in in_window:
        is_rel, topics = evaluate_article_relevance(a, competitor_names=competitor_names)
        if is_rel:
            relevant_articles.append(a)
        else:
            excluded_count += 1

    log.info(
        "[FILTER] Fallback filtering complete.",
        total_candidates=len(articles),
        out_of_date_window=out_of_window_count,
        in_window=len(in_window),
        relevant_matches=len(relevant_articles),
        irrelevant_excluded=excluded_count,
    )

    return relevant_articles
