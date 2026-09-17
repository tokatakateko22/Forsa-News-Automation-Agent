"""
app/search package
──────────────────
Robust search and fallback retrieval subsystem for Forsa News Agent.
Provides SerpAPI quota verification, free Google News RSS query search, publisher RSS,
direct website scraping, article normalization, and relevance filtering.
"""
from app.search.article_normalizer import clean_url, deduplicate_articles, make_normalized_article
from app.search.relevance_filter import evaluate_article_relevance, filter_fallback_articles
from app.search.rss_search import RSSSearchCollector
from app.search.search_manager import SearchManager
from app.search.serpapi_checker import SerpApiStatus, check_serpapi_availability
from app.search.website_search import DirectWebsiteCollector

__all__ = [
    "SearchManager",
    "check_serpapi_availability",
    "SerpApiStatus",
    "clean_url",
    "make_normalized_article",
    "deduplicate_articles",
    "evaluate_article_relevance",
    "filter_fallback_articles",
    "RSSSearchCollector",
    "DirectWebsiteCollector",
]
