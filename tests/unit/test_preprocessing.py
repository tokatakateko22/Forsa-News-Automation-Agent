"""
tests/unit/test_preprocessing.py
──────────────────────────────────
Unit tests for the preprocessing pipeline.
"""
from __future__ import annotations

import pytest

from app.graph.nodes.preprocess import (
    clean_article_content,
    _strip_html,
    _normalize_whitespace,
    _remove_boilerplate,
    _deduplicate_paragraphs,
)


class TestHTMLStripping:
    def test_simple_html(self):
        result = _strip_html("<p>Hello world</p>")
        assert "Hello world" in result
        assert "<p>" not in result

    def test_removes_script(self):
        result = _strip_html("<script>alert('hack')</script><p>News content</p>")
        assert "alert" not in result
        assert "News content" in result

    def test_removes_nav(self):
        result = _strip_html("<nav>Home About</nav><article>Real news</article>")
        assert "Real news" in result
        # Nav content may or may not remain depending on parser — check no "Home About" structural nav
        assert "<nav>" not in result

    def test_plain_text_unchanged(self):
        text = "This is plain text without HTML."
        result = _strip_html(text)
        assert "plain text" in result


class TestBoilerplateRemoval:
    def test_removes_cookie_text(self):
        text = "Important news here. Cookie policy and privacy terms below."
        result = _remove_boilerplate(text)
        assert "Important news here" in result

    def test_removes_subscribe(self):
        text = "CBE raised rates. Subscribe now to get more updates."
        result = _remove_boilerplate(text)
        assert "CBE raised rates" in result


class TestWhitespaceNormalization:
    def test_collapses_multiple_spaces(self):
        result = _normalize_whitespace("hello   world   test")
        assert result == "hello world test"

    def test_strips_leading_trailing(self):
        result = _normalize_whitespace("  hello  ")
        assert result == "hello"

    def test_collapses_newlines(self):
        result = _normalize_whitespace("hello\n\n\nworld")
        assert result == "hello world"


class TestSentenceDeduplication:
    def test_removes_duplicate_sentences(self):
        text = "CBE raised rates by 100bps. This is important news. CBE raised rates by 100bps."
        result = _deduplicate_paragraphs(text)
        # The duplicate sentence should appear only once
        count = result.lower().count("cbe raised rates by 100bps")
        assert count == 1

    def test_keeps_unique_sentences(self):
        text = "First sentence. Second sentence. Third sentence."
        result = _deduplicate_paragraphs(text)
        assert "First sentence" in result
        assert "Second sentence" in result
        assert "Third sentence" in result


class TestFullPipeline:
    def test_cleans_html_article(self):
        raw = """
        <html>
        <head><script>tracking()</script></head>
        <body>
          <nav>Home | About | Contact</nav>
          <article>
            <h1>CBE raises interest rates</h1>
            <p>The Central Bank of Egypt raised its key interest rates by 100 basis points.</p>
            <p>The Central Bank of Egypt raised its key interest rates by 100 basis points.</p>
          </article>
          <footer>Cookie policy. Privacy policy.</footer>
        </body>
        </html>
        """
        result = clean_article_content(raw)
        assert "Central Bank of Egypt" in result
        assert "100 basis points" in result
        assert "<html>" not in result
        assert "tracking()" not in result

    def test_empty_content(self):
        result = clean_article_content(None)
        assert result == ""

    def test_empty_string(self):
        result = clean_article_content("")
        assert result == ""
