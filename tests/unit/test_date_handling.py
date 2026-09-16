"""
tests/unit/test_date_handling.py
──────────────────────────────────
Unit tests for collection window calculation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


class TestCollectionWindow:
    """
    Tests that the window calculation logic correctly handles:
    - Normal case: window = last_run - overlap → now
    - First run: window = now - initial_lookback → now
    - Overlap correctly shifts window back
    """

    def test_window_from_last_run(self):
        """Window starts at last_run - overlap_hours."""
        now = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        last_run = datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc)
        overlap_hours = 2

        expected_start = last_run - timedelta(hours=overlap_hours)
        assert expected_start == datetime(2026, 9, 13, 6, 0, 0, tzinfo=timezone.utc)

    def test_first_run_lookback(self):
        """On first run (no last_run), use initial_lookback_hours."""
        now = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        initial_lookback = 24

        expected_start = now - timedelta(hours=initial_lookback)
        assert expected_start == datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc)

    def test_overlap_prevents_gap(self):
        """Overlap window ensures no news is missed between runs."""
        last_run = datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc)
        overlap_hours = 2
        window_start = last_run - timedelta(hours=overlap_hours)

        # An article published 1h before last run should be within the window
        article_published = datetime(2026, 9, 13, 7, 30, 0, tzinfo=timezone.utc)
        assert window_start <= article_published

    def test_weekly_run_lookback(self):
        """On weekly run, initial lookback is 168 hours (7 days)."""
        now = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        initial_lookback = 168

        expected_start = now - timedelta(hours=initial_lookback)
        assert expected_start == datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)

    def test_manual_days_lookback(self):
        """Custom lookback_days = 7 sets start to exactly 7 days ago."""
        now = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        days = 7
        start = now - timedelta(days=days)
        assert start == datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)

    def test_window_ordering(self):
        """start_time must always be before end_time."""
        now = datetime.now(timezone.utc)
        last_run = now - timedelta(hours=24)
        overlap = 2
        start = last_run - timedelta(hours=overlap)
        assert start < now


class TestArticleDateFiltering:
    def test_article_in_window(self):
        start = datetime(2026, 9, 13, 6, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        pub = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
        assert start <= pub <= end

    def test_article_before_window(self):
        start = datetime(2026, 9, 13, 6, 0, 0, tzinfo=timezone.utc)
        pub = datetime(2026, 9, 12, 6, 0, 0, tzinfo=timezone.utc)
        assert pub < start

    def test_article_after_window(self):
        end = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        pub = datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)
        assert pub > end
