import uuid
from datetime import datetime, timezone
import pytest

from app.models.article import Article
from app.models.event import NewsEvent, EventSummary
from app.graph.nodes.classify import (
    _passes_keyword_filter,
    _deterministic_classify,
    _EXCLUSION_RE,
)
from app.graph.nodes.filter import _has_substance
from app.services.deduplication import DeduplicationService, _is_cbe_rate_story


def make_article(title: str, content: str = "", source_name: str = "Test", source_tier: int = 2) -> Article:
    return Article(
        article_id=str(uuid.uuid4()),
        title=title,
        url=f"https://example.com/{uuid.uuid4()}",
        source_name=source_name,
        source_tier=source_tier,
        published_at=datetime.now(timezone.utc),
        content=content,
        language="ar",
    )


class TestArabicFalsePositives:
    def test_generic_aman_not_classified_as_competitor(self):
        # "أمان" used as "safety"
        art = make_article(
            title="تقرير عن صمام أمان الاقتصاد والتمويل في مصر",
            content="أكد الخبراء على أهمية وجود صمام أمان واستقرار في منظومة التمويل",
        )
        cls = _deterministic_classify(art)
        # Should NOT match Aman competitor
        assert cls is None or cls.competitor_match != "Aman"

    def test_actual_aman_finance_matches_competitor(self):
        art = make_article(
            title="شركة أمان للتمويل تطرح برامج تقسيط جديدة للمستهلكين",
            content="أعلنت شركة أمان للتمويل عن تقديم تسهيلات تقسيط بدون فوائد",
        )
        cls = _deterministic_classify(art)
        assert cls is not None
        assert cls.category == "Competitor"
        assert cls.competitor_match == "Aman"

    def test_generic_halan_adverb_not_classified_as_competitor(self):
        art = make_article(
            title="كيفية الاستعلام عن القرض حالا عبر الإنترنت وبكل سهولة",
            content="يمكن للمواطنين التقديم على التمويل الشخصي والاستعلام حالا بكل سهولة",
        )
        cls = _deterministic_classify(art)
        assert cls is None or cls.competitor_match != "MNT-Halan"

    def test_actual_mnt_halan_matches_competitor(self):
        art = make_article(
            title="إم إن تي حالا تتقدم بطلب قيد أسهمها في البورصة المصرية",
            content="تقدمت شركة حالا للتمويل بطلب رسمي لقيد أسهمها برأسمال 160 مليون جنيه",
        )
        cls = _deterministic_classify(art)
        assert cls is not None
        assert cls.category == "Competitor"
        assert cls.competitor_match == "MNT-Halan"


class TestRegulatorNoiseExclusion:
    def test_student_hackathon_excluded(self):
        art = make_article(
            title="Central Bank of Egypt Launches FinTech Got Talent 2026 Competition for University Students",
            source_name="Central Bank of Egypt",
            source_tier=1,
        )
        assert not _passes_keyword_filter(art)
        cls = _deterministic_classify(art)
        assert cls is not None
        assert cls.is_relevant is False

    def test_fire_insurance_excluded(self):
        art = make_article(
            title="نائب رئيس الرقابة المالية: إعادة هيكلة قطاع تأمين الحريق تتطلب تعزيز هندسة المخاطر",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        assert not _passes_keyword_filter(art)

    def test_routine_t_bill_auction_excluded(self):
        art = make_article(
            title="البنك المركزي يطرح أذون خزانة بقيمة 115 مليار جنيه غدا",
            source_name="SerpAPI Google News",
        )
        assert not _passes_keyword_filter(art)

    def test_routine_t_bond_auction_excluded(self):
        art = make_article(
            title="البنك المركزي يطرح سندات خزانة بقيمة 20 مليار جنيه الإثنين المقبل",
            source_name="SerpAPI Google News",
        )
        assert not _passes_keyword_filter(art)

    def test_assistant_reappointment_excluded(self):
        art = make_article(
            title="تجديد تعيين حمدي بدوي مساعدا لرئيس هيئة الرقابة المالية",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        assert not _passes_keyword_filter(art)

    def test_consumer_finance_fra_decision_retained(self):
        art = make_article(
            title="قرار من الرقابة المالية: إلزام شركات التمويل الاستهلاكي بالربط اللحظي مع الآي سكور",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        assert _passes_keyword_filter(art)
        cls = _deterministic_classify(art)
        assert cls is not None
        assert cls.category == "FRA"
        assert cls.is_relevant is True


class TestStubAndSubstanceFiltering:
    def test_contentless_stub_rejected(self):
        event = NewsEvent(
            event_id=str(uuid.uuid4()),
            canonical_title="قرار لجنة القيد بالبورصة المصرية",
            canonical_url="https://example.com/1",
            canonical_source_name="EGX",
            canonical_source_tier=2,
            category="Financial Market",
            articles=[
                make_article(
                    title="قرار لجنة القيد بالبورصة المصرية",
                    content="",
                )
            ],
            importance_score=75,
            relevance_score=80,
        )
        assert not _has_substance(event)


class TestCBERateDedup:
    def test_cbe_rate_story_detection(self):
        t1 = "تثبيت سعر الفائدة.. ماذا يتوقع الخبراء في اجتماع البنك المركزي المقبل؟"
        t2 = "هيرميس تتوقع تثبيت الفائدة في اجتماع لجنة السياسة النقدية بالبنك المركزي"
        assert _is_cbe_rate_story(t1)
        assert _is_cbe_rate_story(t2)

    def test_cbe_rate_articles_merged_across_days(self):
        a1 = make_article(
            title="تثبيت سعر الفائدة.. توقعات اجتماع البنك المركزي المقبل في 24 سبتمبر",
            content="يتوقع الخبراء تثبيت الفائدة عند 19% للإيداع و20% للإقراض مع تراجع التضخم إلى 14.5%",
            source_name="Iskan Misr",
        )
        a2 = make_article(
            title="هيرميس تتوقع تثبيت الفائدة في اجتماع البنك المركزي القادم",
            content="تتوقع المجموعة المالية هيرميس إبقاء أسعار الفائدة دون تغيير",
            source_name="Economy Plus",
        )
        service = DeduplicationService()
        cls1 = _deterministic_classify(a1)
        cls2 = _deterministic_classify(a2)
        events = service.deduplicate([a1, a2], {a1.article_id: cls1, a2.article_id: cls2})
        # Must be merged into ONE single event
        assert len(events) == 1
        # The canonical event must be the richer article (a1)
        assert events[0].canonical_title == a1.title


class TestRegulatoryTopicIsolation:
    def test_iscore_and_otp_not_merged(self):
        a_iscore = make_article(
            title="عاجل.. الرقابة المالية تلزم شركات التمويل الاستهلاكي وتمويل المشروعات بالربط اللحظي مع «آي سكور»",
            content="أصدرت الهيئة العامة للرقابة المالية قرارين رقم 174 و175 يلزمان بالربط اللحظي مع آي سكور",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        a_otp = make_article(
            title="الرقابة المالية تلزم شركات التمويل الاستهلاكي وتمويل المشروعات باستخدام OTP للتحقق من هوية العملاء خلال شهرين",
            content="ألزمت الرقابة المالية الشركات بتطبيق رمز التحقق لمرة واحدة OTP",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        service = DeduplicationService()
        cls1 = _deterministic_classify(a_iscore)
        cls2 = _deterministic_classify(a_otp)
        events = service.deduplicate([a_iscore, a_otp], {a_iscore.article_id: cls1, a_otp.article_id: cls2})
        # MUST remain two distinct events
        assert len(events) == 2

    def test_iscore_and_valuation_not_merged(self):
        a_iscore = make_article(
            title="قرار من “الرقابة المالية” بالربط اللحظي بين شركات وجهات التمويل الاستهلاكي وتمويل المشروعات مع “آي سكور”",
            content="الربط اللحظي مع شركة الاستعلام الائتماني",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        a_val = make_article(
            title="“الرقابة المالية” برئاسة د. إسلام عزام تصدر المعايير المصرية للتقييم العقاري – السبت 12 سبتمبر 2026",
            content="إصدار المعايير المصرية للتقييم العقاري المتوافقة مع المعايير الدولية 2025",
            source_name="Financial Regulatory Authority",
            source_tier=1,
        )
        service = DeduplicationService()
        cls1 = _deterministic_classify(a_iscore)
        cls2 = _deterministic_classify(a_val)
        events = service.deduplicate([a_iscore, a_val], {a_iscore.article_id: cls1, a_val.article_id: cls2})
        assert len(events) == 2

    def test_two_iscore_articles_merged(self):
        a1 = make_article(
            title="عاجل.. الرقابة المالية تلزم شركات التمويل الاستهلاكي بالربط اللحظي مع «آي سكور»",
            content="قرار رسمي يلزم بالربط اللحظي الإلكتروني خلال مهلة 3 أشهر لجميع المراحل الائتمانية",
            source_name="Al Borsa",
        )
        a2 = make_article(
            title="مصر تلزم شركات التمويل الاستهلاكي بالربط اللحظى مع «آي سكور» خلال 3 أشهر",
            content="تفاصيل قرار الرقابة المالية بالربط اللحظي لشركات التمويل الاستهلاكي",
            source_name="Economy Plus",
        )
        service = DeduplicationService()
        cls1 = _deterministic_classify(a1)
        cls2 = _deterministic_classify(a2)
        events = service.deduplicate([a1, a2], {a1.article_id: cls1, a2.article_id: cls2})
        assert len(events) == 1

    def test_global_paradigm_ollin_classification(self):
        art = make_article(
            title="بعد أزمة جلوبال بارادايم.. الرقابة المالية تحيل شركة أولين للتمويل الاستهلاكي للنيابة العامة",
            content="إحالة شركة أولين للتمويل الاستهلاكي التابعة لجلوبال كورب للنيابة في واقعة قروض 319 مليون جنيه",
        )
        cls = _deterministic_classify(art)
        assert cls is not None
        assert cls.category == "FRA"
        assert cls.is_relevant is True
        assert cls.importance_score >= 90

    def test_supervisory_manual_classification(self):
        art = make_article(
            title="الرقابة المالية تصدر أول دليل شامل لقواعد وضوابط التمويل الاستهلاكي وتحدد سقف عبء الدين بـ50%",
            content="أصدرت الهيئة العامة للرقابة المالية دليلا إشرافيا شاملا يحدد سقف الأقساط بـ50% من الدخل والتمويل النقدي بـ50 ألف جنيه",
        )
        cls = _deterministic_classify(art)
        assert cls is not None
        assert cls.category == "FRA"
        assert cls.is_relevant is True
        assert cls.importance_score >= 90


class TestBilingualAndTopicDeduplication:
    def test_bilingual_fra_url_normalized_dedup(self):
        # Arabic FRA release and English FRA release with identical path except /en/
        a_ar = Article(
            article_id=str(uuid.uuid4()),
            title="الرقابة المالية برئاسة د. إسلام عزام تصدر الإصدار الثاني من المعايير المصرية للتقييم العقاري",
            url="https://fra.gov.eg/fra_news/%d8%a7%d9%84%d8%b1%d9%82%d8%a7%d8%a8%d8%a9-%d8%a7%d9%84%d9%85%d8%a7%d9%84%d9%8a%d8%a9-%d8%a8%d8%b1%d8%a6%d8%a7%d8%b3%d8%a9-%d8%af-%d8%a5%d8%b3%d9%84%d8%a7%d9%85-%d8%b9%d8%b2%d8%a7%d9%85-%d8%aa-5/",
            source_name="Financial Regulatory Authority",
            source_tier=1,
            published_at=datetime.now(timezone.utc),
            content="أصدرت الهيئة العامة للرقابة المالية قرار رقم 191 لسنة 2026 بالمعايير المصرية للتقييم العقاري",
            language="ar",
        )
        a_en = Article(
            article_id=str(uuid.uuid4()),
            title="FRA Issues Updated Egyptian Real Estate Valuation Standards",
            url="https://fra.gov.eg/en/fra_news/%d8%a7%d9%84%d8%b1%d9%82%d8%a7%d8%a8%d8%a9-%d8%a7%d9%84%d9%85%d8%a7%d9%84%d9%8a%d8%a9-%d8%a8%d8%b1%d8%a6%d8%a7%d8%b3%d8%a9-%d8%af-%d8%a5%d8%b3%d9%84%d8%a7%d9%85-%d8%b9%d8%b2%d8%a7%d9%85-%d8%aa-5/",
            source_name="Financial Regulatory Authority (EN)",
            source_tier=1,
            published_at=datetime.now(timezone.utc),
            content="The FRA issued Board Decision No. 191 of 2026 issuing the second edition of the Egyptian Real Estate Valuation Standards",
            language="en",
        )
        service = DeduplicationService()
        cls_ar = _deterministic_classify(a_ar)
        cls_en = _deterministic_classify(a_en)
        events = service.deduplicate([a_ar, a_en], {a_ar.article_id: cls_ar, a_en.article_id: cls_en})
        # Must be unified into a single event via URL normalization
        assert len(events) == 1
        # English source is kept over Arabic source
        assert events[0].canonical_url == a_en.url
        assert events[0].canonical_title == a_en.title

    def test_arabic_and_english_iscore_decisions_merged(self):
        # Arabic decree and English SeeNews article
        a_ar = Article(
            article_id=str(uuid.uuid4()),
            title="قرار من “الرقابة المالية” بالربط اللحظي بين شركات وجهات التمويل الاستهلاكي وتمويل المشروعات مع “آي سكور”",
            url="https://fra.gov.eg/fra_news/iscore-decision-174",
            source_name="Financial Regulatory Authority",
            source_tier=1,
            published_at=datetime.now(timezone.utc),
            content="أصدرت الهيئة العامة للرقابة المالية القرارين 174 و 175 بإلزام شركات التمويل الاستهلاكي بالربط اللحظي مع آي سكور",
            language="ar",
        )
        a_en = Article(
            article_id=str(uuid.uuid4()),
            title="Egypt’s FRA tightens foreign-currency financing, credit reporting rules",
            url="https://see.news/egypts-fra-tightens-foreign-currency-financing-credit-reporting-rules",
            source_name="SeeNews",
            source_tier=2,
            published_at=datetime.now(timezone.utc),
            content="Under FRA Decision No. 174 of 2026, licensed consumer finance companies must execute real-time credit reporting to bureaus",
            language="en",
        )
        service = DeduplicationService()
        cls_ar = _deterministic_classify(a_ar)
        cls_en = _deterministic_classify(a_en)
        events = service.deduplicate([a_ar, a_en], {a_ar.article_id: cls_ar, a_en.article_id: cls_en})
        # Must be unified into a single event
        assert len(events) == 1
        # English source is kept over Arabic source
        assert events[0].canonical_url == a_en.url
        assert events[0].canonical_title == a_en.title

    def test_cbe_rates_story_with_inflation_not_merged_with_cpi_release(self):
        a_rate = Article(
            article_id=str(uuid.uuid4()),
            title="المركزي المصري أمام اختبار التضخم والوقود.. لماذا يتقدم سيناريو تثبيت الفائدة؟",
            url="https://mubasher.info/news/rate-preview",
            source_name="Mubasher",
            source_tier=2,
            published_at=datetime.now(timezone.utc),
            content="تترقب الأسواق اجتماع لجنة السياسة النقدية بالبنك المركزي المصري يوم 24 سبتمبر وتثبيت الفائدة",
            language="ar",
        )
        a_cpi = Article(
            article_id=str(uuid.uuid4()),
            title="CPI Press Release August 2026",
            url="https://www.cbe.org.eg/en/news-publications/news/2026/09/10/cpi-press-release-august-2026",
            source_name="Central Bank of Egypt",
            source_tier=1,
            published_at=datetime.now(timezone.utc),
            content="Monthly urban headline CPI inflation recorded 0.1 percent in August 2026, annual rate 14.5 percent",
            language="en",
        )
        service = DeduplicationService()
        cls_rate = _deterministic_classify(a_rate)
        cls_cpi = _deterministic_classify(a_cpi)
        events = service.deduplicate([a_rate, a_cpi], {a_rate.article_id: cls_rate, a_cpi.article_id: cls_cpi})
        # Must remain two distinct events (CBE_RATES vs INFLATION)
        assert len(events) == 2


class TestExecutiveQualityGuarantees:
    def test_normalize_url_handles_protocols_and_subpaths(self):
        from app.services.deduplication import _normalize_url
        u1 = "https://fra.gov.eg/en/fra_news/decision-191/"
        u2 = "http://www.fra.gov.eg/fra_news/decision-191?ref=feed#sec"
        u3 = "https://fra.gov.eg/fra_news/decision-191"
        assert _normalize_url(u1) == _normalize_url(u2) == _normalize_url(u3)

    def test_cross_lingual_competitor_campaign_dedup(self):
        a_ar = Article(
            article_id=str(uuid.uuid4()),
            title="فاليو تتيح تقسيط الجمارك والضرائب عبر منصتها الرقمية للمواطنين",
            url="https://alborsaanews.com/valu-customs-installments",
            source_name="Al Borsa",
            source_tier=2,
            published_at=datetime.now(timezone.utc),
            content="أعلنت شركة فاليو لحلول التمويل الاستهلاكي عن إتاحة تقسيط الجمارك والضرائب على مشتريات الأفراد",
            language="ar",
        )
        a_en = Article(
            article_id=str(uuid.uuid4()),
            title="valU introduces installment plans for customs duties and taxes",
            url="https://dailynewsegypt.com/valu-customs-duties",
            source_name="Daily News Egypt",
            source_tier=2,
            published_at=datetime.now(timezone.utc),
            content="Consumer finance platform valU announced that customers can now finance their customs duties and taxes via flexible installment options.",
            language="en",
        )
        service = DeduplicationService()
        cls_ar = _deterministic_classify(a_ar)
        cls_en = _deterministic_classify(a_en)
        events = service.deduplicate([a_ar, a_en], {a_ar.article_id: cls_ar, a_en.article_id: cls_en})
        assert len(events) == 1
        assert events[0].canonical_url == a_en.url
        assert events[0].canonical_title == a_en.title

    def test_substance_filter_strictly_drops_under_80_chars(self):
        event = NewsEvent(
            event_id=str(uuid.uuid4()),
            canonical_title="البنك المركزي المصري يعلن نتائج اجتماع لجنة السياسة النقدية لعام 2026",
            canonical_url="https://example.com/cbe-stub",
            canonical_source_name="CBE",
            canonical_source_tier=1,
            category="CBE",
            articles=[
                make_article(
                    title="البنك المركزي المصري يعلن نتائج اجتماع لجنة السياسة النقدية لعام 2026",
                    content="خبر عاجل من البنك المركزي المصري.",  # Only 33 characters
                )
            ],
            importance_score=85,
            relevance_score=90,
        )
        assert not _has_substance(event)

    @pytest.mark.asyncio
    async def test_summarize_rejects_garbled_output(self, monkeypatch):
        from app.graph.nodes.summarize import _summarize_event

        async def mock_summarize(title, source, content):
            return ")?* Yes (CBE, Sept 24, 19%, 20%, 19.5%, August inflation 14.5% and 14.9"

        monkeypatch.setattr("app.services.llm.summarize_article", mock_summarize)
        event = NewsEvent(
            event_id=str(uuid.uuid4()),
            canonical_title="CBE MPC Meeting",
            canonical_url="https://example.com/cbe",
            canonical_source_name="CBE",
            canonical_source_tier=1,
            category="CBE",
            articles=[make_article("CBE MPC Meeting", "Content " * 20)],
        )
        res = await _summarize_event(event)
        assert res.summary is None

    @pytest.mark.asyncio
    async def test_summarize_rejects_arabic_leakage(self, monkeypatch):
        from app.graph.nodes.summarize import _summarize_event

        async def mock_summarize(title, source, content):
            return "قررت الهيئة العامة للرقابة المالية إلزام شركات التمويل الاستهلاكي بالربط اللحظي."

        monkeypatch.setattr("app.services.llm.summarize_article", mock_summarize)
        event = NewsEvent(
            event_id=str(uuid.uuid4()),
            canonical_title="FRA Real-Time Decision",
            canonical_url="https://fra.gov.eg/decision",
            canonical_source_name="FRA",
            canonical_source_tier=1,
            category="FRA",
            articles=[make_article("FRA Decision", "Content " * 20)],
        )
        res = await _summarize_event(event)
        assert res.summary is None

    def test_format_category_label_enforces_english(self):
        from app.graph.nodes.format_email import _format_category_label

        summary = EventSummary(
            event_id="e1",
            summary_text="Summary text in English.",
            category="Competitor",
            canonical_title="valU promo",
            canonical_url="https://example.com",
            source_name="valU",
            competitor_name="فاليو",
        )
        event = NewsEvent(
            event_id="e1",
            canonical_title="valU promo",
            canonical_url="https://example.com",
            canonical_source_name="valU",
            canonical_source_tier=2,
            category="Competitor",
            articles=[],
            summary=summary,
        )
        label = _format_category_label(event, summary)
        assert label == "Competitor — valU"
        assert not any("\u0600" <= c <= "\u06ff" for c in label)


