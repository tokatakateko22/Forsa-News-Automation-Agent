"""
tests/unit/test_search_fallback.py
──────────────────────────────────
Unit tests for the SerpAPI quota detection, Google News / Publisher RSS fallback,
direct website scraping, article normalization, and relevance filtering.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.models.article import Article
from app.search.article_normalizer import (
    clean_url,
    deduplicate_articles,
    make_normalized_article,
    normalize_title_for_dedup,
    parse_datetime,
)
from app.search.relevance_filter import (
    evaluate_article_relevance,
    filter_fallback_articles,
)
from app.search.search_manager import SearchManager
from app.search.serpapi_checker import SerpApiStatus, check_serpapi_availability


class TestSerpAPIChecker(unittest.IsolatedAsyncioTestCase):
    """Test SerpAPI pre-flight availability and quota checks."""

    async def test_forced_by_configuration(self):
        status = await check_serpapi_availability(force_fallback=True)
        self.assertFalse(status.is_available)
        self.assertEqual(status.reason, "forced_by_configuration")
        self.assertEqual(status.searches_remaining, 0)

    async def test_empty_api_key(self):
        status = await check_serpapi_availability(api_key="", force_fallback=False)
        self.assertFalse(status.is_available)
        self.assertEqual(status.reason, "api_key_empty")

    @patch("httpx.AsyncClient.get")
    async def test_quota_available(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_searches_left": 120,
            "account_status": "Active",
            "account_email": "finance@drive-finance.com",
        }
        mock_get.return_value = mock_resp

        status = await check_serpapi_availability(api_key="valid_test_key", force_fallback=False)
        self.assertTrue(status.is_available)
        self.assertEqual(status.reason, "quota_available")
        self.assertEqual(status.searches_remaining, 120)
        self.assertEqual(status.account_email, "finance@drive-finance.com")

    @patch("httpx.AsyncClient.get")
    async def test_quota_exhausted_zero_left(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_searches_left": 0,
            "account_status": "Your account has run out of searches.",
            "account_email": "finance@drive-finance.com",
        }
        mock_get.return_value = mock_resp

        status = await check_serpapi_availability(api_key="exhausted_key", force_fallback=False)
        self.assertFalse(status.is_available)
        self.assertEqual(status.reason, "quota_exhausted")
        self.assertEqual(status.searches_remaining, 0)

    @patch("httpx.AsyncClient.get")
    async def test_quota_insufficient_below_threshold(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "total_searches_left": 2,
            "account_status": "Active",
        }
        mock_get.return_value = mock_resp

        status = await check_serpapi_availability(
            api_key="low_key",
            force_fallback=False,
            min_searches=5,
        )
        self.assertFalse(status.is_available)
        self.assertIn("insufficient_quota", status.reason)

    @patch("httpx.AsyncClient.get")
    async def test_rate_limit_429(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        status = await check_serpapi_availability(api_key="test_key", force_fallback=False)
        self.assertFalse(status.is_available)
        self.assertEqual(status.reason, "rate_limited_429")

    @patch("httpx.AsyncClient.get")
    async def test_auth_error_401(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        status = await check_serpapi_availability(api_key="invalid_key", force_fallback=False)
        self.assertFalse(status.is_available)
        self.assertEqual(status.reason, "authentication_error")

    @patch("httpx.AsyncClient.get")
    async def test_connection_error(self, mock_get):
        mock_get.side_effect = httpx.ConnectTimeout("Connection timed out")

        status = await check_serpapi_availability(api_key="test_key", force_fallback=False)
        self.assertFalse(status.is_available)
        self.assertIn("connection_error", status.reason)


class TestArticleNormalizer(unittest.TestCase):
    """Test URL cleaning, date parsing, article construction, and deduplication."""

    def test_clean_url_strips_tracking(self):
        raw = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123&fbclid=XYZ#header"
        cleaned = clean_url(raw)
        self.assertEqual(cleaned, "https://example.com/article?id=123")

    def test_parse_datetime_rss_string(self):
        rss_date = "Thu, 17 Sep 2026 08:30:00 +0300"
        dt = parse_datetime(rss_date)
        self.assertIsNotNone(dt)
        assert dt is not None
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 5)  # 08:30 +03:00 -> 05:30 UTC

    def test_make_normalized_article_invalid(self):
        self.assertIsNone(make_normalized_article(title="", url="https://a.com", source_name="Test"))
        self.assertIsNone(make_normalized_article(title="Title", url="", source_name="Test"))

    def test_make_normalized_article_valid(self):
        art = make_normalized_article(
            title="<b>Consumer Finance Growing in Egypt</b>",
            url="https://dailynewsegypt.com/cf-growth/?utm_source=rss",
            source_name="Daily News Egypt",
            content="<p>Retail lending surged by 15%.</p>",
            published_at="2026-09-17T08:00:00Z",
        )
        self.assertIsNotNone(art)
        assert art is not None
        self.assertEqual(art.title, "Consumer Finance Growing in Egypt")
        self.assertEqual(art.url, "https://dailynewsegypt.com/cf-growth")
        self.assertEqual(art.content, "Retail lending surged by 15%.")
        self.assertEqual(art.language, "en")
        self.assertIsNotNone(art.content_hash)

    def test_deduplicate_articles(self):
        a1 = make_normalized_article(
            title="Valu launches new financing program - Daily News Egypt",
            url="https://example.com/news/1?utm_source=fb",
            source_name="Source A",
            content="Content A",
        )
        # Duplicate with different tracking param
        a2 = make_normalized_article(
            title="Valu launches new financing program | Daily News",
            url="https://example.com/news/1?utm_source=twitter",
            source_name="Source B",
            content="Content A",
        )
        # Unique article
        a3 = make_normalized_article(
            title="CBE holds interest rates steady",
            url="https://example.com/news/2",
            source_name="Source C",
            content="Content C",
        )
        assert a1 is not None and a2 is not None and a3 is not None
        deduped = deduplicate_articles([a1, a2, a3])
        self.assertEqual(len(deduped), 2)
        urls = {a.url for a in deduped}
        self.assertIn("https://example.com/news/1", urls)
        self.assertIn("https://example.com/news/2", urls)


class TestRelevanceFilter(unittest.TestCase):
    """Test thematic relevance filtering for fallback articles."""

    def test_consumer_finance_accepted(self):
        art = Article(
            title="Egyptian consumer finance companies report surging installment demand",
            url="https://example.com/1",
            source_name="Test",
            content="Retail lending and BNPL options saw higher adoption among middle-income households.",
        )
        is_rel, topics = evaluate_article_relevance(art)
        self.assertTrue(is_rel)
        self.assertIn("consumer_finance", topics)

    def test_forsa_accepted(self):
        art = Article(
            title="فرصة للتمويل تطلق حلول تقسيط رقمية جديدة",
            url="https://example.com/2",
            source_name="Test",
            content="أعلنت شركة فرصة للتمويل الاستهلاكي عن خطط جديدة.",
            language="ar",
        )
        is_rel, topics = evaluate_article_relevance(art)
        self.assertTrue(is_rel)
        self.assertIn("forsa", topics)

    def test_competitor_accepted(self):
        art = Article(
            title="شركة فاليو توقع شراكة لتقسيط المشتريات",
            url="https://example.com/3",
            source_name="Test",
            content="أعلنت شركة فاليو للتمويل الاستهلاكي عن برنامج تمويلي.",
            language="ar",
        )
        is_rel, topics = evaluate_article_relevance(art)
        self.assertTrue(is_rel)
        self.assertIn("competitor", topics)

    def test_regulatory_cbe_accepted(self):
        art = Article(
            title="Central Bank of Egypt holds monetary policy meeting on interest rates",
            url="https://example.com/4",
            source_name="Test",
            content="The Monetary Policy Committee decided to keep the corridor overnight deposit rate unchanged.",
        )
        is_rel, topics = evaluate_article_relevance(art)
        self.assertTrue(is_rel)
        self.assertIn("regulatory", topics)

    def test_fintech_accepted(self):
        art = Article(
            title="Egypt FinTech startup raises funding for digital onboarding and e-KYC",
            url="https://example.com/5",
            source_name="Test",
            content="The platform enables instant digital identity verification for retail banking.",
        )
        is_rel, topics = evaluate_article_relevance(art)
        self.assertTrue(is_rel)
        self.assertIn("fintech", topics)

    def test_exclusions_rejected(self):
        # Student competition / hackathon
        art1 = Article(
            title="University students participate in FinTech hackathon got talent",
            url="https://example.com/e1",
            source_name="Test",
            content="Winners of the university competition awarded prizes.",
        )
        self.assertFalse(evaluate_article_relevance(art1)[0])

        # Fire insurance
        art2 = Article(
            title="الهيئة تبحث مجمعة تأمين الحريق للمنشآت",
            url="https://example.com/e2",
            source_name="Test",
            content="تقرير حول تأمين الحريق والمجمعات التأمينية.",
        )
        self.assertFalse(evaluate_article_relevance(art2)[0])

        # Routine treasury bill auction
        art3 = Article(
            title="المركزي يطرح أذون خزانة بقيمة 50 مليار جنيه في عطاء دوري",
            url="https://example.com/e3",
            source_name="Test",
            content="طرح دوري معتاد لأذون الخزانة.",
        )
        self.assertFalse(evaluate_article_relevance(art3)[0])

    def test_general_noise_rejected(self):
        art = Article(
            title="افتتاح مهرجان السياحة والتسوق في الغردقة بحضور وزير السياحة",
            url="https://example.com/g1",
            source_name="Test",
            content="حفل الافتتاح شهد فعاليات فنية وثقافية متنوعة.",
        )
        self.assertFalse(evaluate_article_relevance(art)[0])

    def test_date_window_filtering(self):
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)
        end = now

        in_win = Article(
            title="Consumer finance growth",
            url="https://example.com/in",
            source_name="Test",
            content="BNPL installment demand surged.",
            published_at=now - timedelta(days=2),
        )
        too_old = Article(
            title="Consumer finance old update",
            url="https://example.com/old",
            source_name="Test",
            content="BNPL installment demand surged.",
            published_at=now - timedelta(days=15),
        )
        filtered = filter_fallback_articles([in_win, too_old], start, end)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].url, "https://example.com/in")


class TestSearchManager(unittest.IsolatedAsyncioTestCase):
    """Test unified search manager orchestration and fallback trigger."""

    @patch("app.search.search_manager.check_serpapi_availability")
    async def test_uses_serpapi_when_available(self, mock_check):
        mock_check.return_value = SerpApiStatus(
            is_available=True,
            reason="quota_available",
            searches_remaining=50,
        )

        manager = SearchManager()
        manager._run_serpapi = AsyncMock(return_value=[
            Article(title="SerpAPI Article", url="https://example.com/serp", source_name="Google News")
        ])
        manager._run_fallback = AsyncMock()

        now = datetime.now(timezone.utc)
        articles = await manager.collect_news(now - timedelta(days=7), now)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "SerpAPI Article")
        manager._run_serpapi.assert_awaited_once()
        manager._run_fallback.assert_not_awaited()

    @patch("app.search.search_manager.check_serpapi_availability")
    async def test_uses_fallback_when_exhausted(self, mock_check):
        mock_check.return_value = SerpApiStatus(
            is_available=False,
            reason="quota_exhausted",
            searches_remaining=0,
        )

        manager = SearchManager()
        manager._run_serpapi = AsyncMock()
        manager._run_fallback = AsyncMock(return_value=[
            Article(title="Fallback RSS Article", url="https://example.com/fb", source_name="Daily News Egypt")
        ])

        now = datetime.now(timezone.utc)
        articles = await manager.collect_news(now - timedelta(days=7), now)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "Fallback RSS Article")
        manager._run_serpapi.assert_not_awaited()
        manager._run_fallback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
