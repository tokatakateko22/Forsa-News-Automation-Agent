"""
tests/unit/test_no_news_behaviour.py
────────────────────────────────────
Automated tests for news delivery behavior:
1. No important events path (NO_NEWS_BEHAVIOUR=skip):
   - Skips formatting and sending email
   - Does NOT call Power Automate webhook / email service
   - Sets email_sent to False
   - Completes workflow run in DB with zero events sent
   - Does NOT mark any events as sent in DB
   - Logs clear message explaining delivery was skipped
   - route_after_no_news routes to END
2. Important events exist path:
   - Normal email delivery unchanged
   - format_email builds digest
   - Calls email service / Power Automate webhook
   - Sets email_sent to True
   - Marks events as sent in DB
   - Completes workflow run with events_sent = count
3. Configurable send_empty path:
   - When explicitly configured as 'send_empty', builds empty notice
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.config import settings
from app.graph.nodes.filter import route_after_filter
from app.graph.nodes.format_email import handle_no_news, format_email, route_after_no_news
from app.graph.nodes.send_email import send_email
from app.graph.state import AgentState
from app.models.event import NewsEvent, EventSummary
from app.models.article import Article


def _make_sample_state(
    important_events: list[NewsEvent] | None = None,
    run_id: str | None = None,
) -> AgentState:
    rid = run_id or str(uuid.uuid4())
    return {
        "run_id": rid,
        "started_at": datetime.now(timezone.utc),
        "collection_start": datetime.now(timezone.utc),
        "collection_end": datetime.now(timezone.utc),
        "ignore_already_sent": False,
        "force_search_fallback": False,
        "raw_articles": [],
        "clean_articles": [],
        "classifications": {},
        "relevant_articles": [],
        "events": [],
        "important_events": important_events or [],
        "email_subject": "",
        "email_html": "",
        "email_plain": "",
        "email_sent": False,
        "errors": [],
        "stats": {
            "articles_collected": 10,
            "articles_relevant": 3,
            "articles_deduplicated": 1,
            "articles_verified": 2,
            "events_detected": 2,
            "events_sent": 0,
            "summaries_generated": 0,
            "email_sent": False,
        },
    }


def _make_sample_event(title: str = "FRA issues new credit decree") -> NewsEvent:
    event_id = str(uuid.uuid4())
    summary = EventSummary(
        event_id=event_id,
        summary_text="The FRA issued new regulatory rules for non-bank lending.",
        category="FRA",
        canonical_title=title,
        canonical_url="https://fra.gov.eg/news/1",
        source_name="Financial Regulatory Authority",
        published_at=datetime.now(timezone.utc),
    )
    article = Article(
        article_id=str(uuid.uuid4()),
        title=title,
        url="https://fra.gov.eg/news/1",
        source_name="Financial Regulatory Authority",
        source_tier=1,
        published_at=datetime.now(timezone.utc),
    )
    return NewsEvent(
        event_id=event_id,
        canonical_title=title,
        event_date=datetime.now(timezone.utc),
        category="FRA",
        canonical_url="https://fra.gov.eg/news/1",
        canonical_source_name="Financial Regulatory Authority",
        canonical_source_tier=1,
        articles=[article],
        summary=summary,
        importance_score=85,
        relevance_score=90,
        verification_status="OFFICIAL",
        should_send=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. No Important Events Path (NO_NEWS_BEHAVIOUR = skip)
# ══════════════════════════════════════════════════════════════════════════════

class TestNoNewsSkipPath:
    """When no important news events are found and NO_NEWS_BEHAVIOUR is skip."""

    @pytest.mark.asyncio
    async def test_handle_no_news_sets_email_sent_false_and_clears_subject(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        state = _make_sample_state(important_events=[])

        with patch("app.graph.nodes.format_email.get_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session
            with patch("app.graph.nodes.format_email.WorkflowRunRepository") as mock_run_repo_cls:
                mock_run_repo = AsyncMock()
                mock_run_repo_cls.return_value = mock_run_repo

                result = await handle_no_news(state)

                assert result["email_sent"] is False
                assert result["email_subject"] == ""
                assert result["email_html"] == ""
                assert result["email_plain"] == ""
                assert result["important_events"] == []
                assert result["stats"]["email_sent"] is False
                assert result["stats"]["events_sent"] == 0

    @pytest.mark.asyncio
    async def test_handle_no_news_logs_clear_skip_message(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        state = _make_sample_state(important_events=[])

        with patch("app.graph.nodes.format_email.log") as mock_log:
            with patch("app.graph.nodes.format_email.get_session"):
                await handle_no_news(state)

                # Verify clear log entry is recorded
                assert mock_log.info.called
                log_calls = [call for call in mock_log.info.call_args_list]
                skip_logs = [c for c in log_calls if c.args and c.args[0] == "email.delivery_skipped"]
                assert len(skip_logs) == 1
                kwargs = skip_logs[0].kwargs
                assert kwargs["reason"] == "no_important_news"
                assert "skipped because no important news was found" in kwargs["message"]

    @pytest.mark.asyncio
    async def test_handle_no_news_completes_workflow_run_with_zero_events(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        run_id = str(uuid.uuid4())
        state = _make_sample_state(important_events=[], run_id=run_id)

        with patch("app.graph.nodes.format_email.get_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session
            with patch("app.graph.nodes.format_email.WorkflowRunRepository") as mock_run_repo_cls:
                mock_run_repo = AsyncMock()
                mock_run_repo_cls.return_value = mock_run_repo

                await handle_no_news(state)

                mock_run_repo.complete.assert_called_once()
                call_kwargs = mock_run_repo.complete.call_args.kwargs
                assert call_kwargs["run_id"] == uuid.UUID(run_id)
                assert call_kwargs["events_sent"] == 0
                assert call_kwargs["email_sent"] is False
                assert call_kwargs["error_message"] is None

    def test_route_after_no_news_terminates_at_end_when_skip(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        state = _make_sample_state(important_events=[])
        assert route_after_no_news(state) == "end"

    @pytest.mark.asyncio
    async def test_format_email_skips_when_no_important_events(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        state = _make_sample_state(important_events=[])

        result = await format_email(state)
        assert result["email_subject"] == ""
        assert result["email_html"] == ""
        assert result["email_plain"] == ""
        assert result["email_sent"] is False

    @pytest.mark.asyncio
    async def test_send_email_node_does_not_call_webhook_when_no_news(self, monkeypatch):
        """Even if send_email is directly called with no subject/events, it must skip sending."""
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        state = _make_sample_state(important_events=[])
        state["email_subject"] = ""

        with patch("app.graph.nodes.send_email.email_service.send") as mock_email_send:
            with patch("app.graph.nodes.send_email._persist_events") as mock_persist_events:
                with patch("app.graph.nodes.send_email.get_session") as mock_session_ctx:
                    mock_session = AsyncMock()
                    mock_session_ctx.return_value.__aenter__.return_value = mock_session
                    with patch("app.graph.nodes.send_email.WorkflowRunRepository") as mock_run_repo_cls:
                        mock_run_repo = AsyncMock()
                        mock_run_repo_cls.return_value = mock_run_repo

                        result = await send_email(state)

                        # Webhook / SMTP must NOT be called
                        mock_email_send.assert_not_called()
                        # Sent events must NOT be recorded
                        mock_persist_events.assert_not_called()
                        # Run must be completed with 0 events
                        assert result["email_sent"] is False
                        assert result["stats"]["events_sent"] == 0
                        mock_run_repo.complete.assert_called_once()
                        assert mock_run_repo.complete.call_args.kwargs["events_sent"] == 0
                        assert mock_run_repo.complete.call_args.kwargs["email_sent"] is False


# ══════════════════════════════════════════════════════════════════════════════
# 2. Important Events Exist Path (Normal Delivery)
# ══════════════════════════════════════════════════════════════════════════════

class TestImportantNewsExistPath:
    """When one or more important news events exist for a run."""

    def test_route_after_filter_routes_to_has_news(self):
        event = _make_sample_event()
        state = _make_sample_state(important_events=[event])
        assert route_after_filter(state) == "has_news"

    @pytest.mark.asyncio
    async def test_format_email_builds_digest_with_events(self):
        event = _make_sample_event("Central Bank of Egypt raised rates")
        state = _make_sample_state(important_events=[event])

        result = await format_email(state)
        assert result["email_subject"] != ""
        assert "Financial Market News" in result["email_subject"]
        assert "The FRA issued new regulatory rules for non-bank lending." in result["email_plain"]
        assert "https://fra.gov.eg/news/1" in result["email_plain"]
        assert "The FRA issued new regulatory rules for non-bank lending." in result["email_html"]

    @pytest.mark.asyncio
    async def test_send_email_calls_service_and_records_sent_events(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "skip")
        event = _make_sample_event("FRA decrees new microfinance rule")
        run_id = str(uuid.uuid4())
        state = _make_sample_state(important_events=[event], run_id=run_id)
        state["email_subject"] = "Financial Market News — 14 September 2026"
        state["email_html"] = "<p>FRA decrees new rule</p>"
        state["email_plain"] = "FRA decrees new rule"

        with patch("app.graph.nodes.send_email.email_service.send") as mock_email_send:
            with patch("app.graph.nodes.send_email._persist_events", new_callable=AsyncMock) as mock_persist_events:
                with patch("app.graph.nodes.send_email.get_session") as mock_session_ctx:
                    mock_session = AsyncMock()
                    mock_session_ctx.return_value.__aenter__.return_value = mock_session
                    with patch("app.graph.nodes.send_email.WorkflowRunRepository") as mock_run_repo_cls:
                        mock_run_repo = AsyncMock()
                        mock_run_repo_cls.return_value = mock_run_repo

                        result = await send_email(state)

                        # Email delivery service / webhook MUST be called
                        mock_email_send.assert_called_once_with(
                            subject="Financial Market News — 14 September 2026",
                            html_body="<p>FRA decrees new rule</p>",
                            plain_body="FRA decrees new rule",
                            recipient=settings.email_recipient,
                        )

                        # Events MUST be persisted as sent
                        mock_persist_events.assert_called_once_with([event], run_id)

                        # State updated
                        assert result["email_sent"] is True
                        assert result["stats"]["email_sent"] is True
                        assert result["stats"]["events_sent"] == 1

                        # Workflow run completed with events_sent = 1
                        mock_run_repo.complete.assert_called_once()
                        assert mock_run_repo.complete.call_args.kwargs["events_sent"] == 1
                        assert mock_run_repo.complete.call_args.kwargs["email_sent"] is True


# ══════════════════════════════════════════════════════════════════════════════
# 3. Optional 'send_empty' Behaviour Path
# ══════════════════════════════════════════════════════════════════════════════

class TestSendEmptyPath:
    """When NO_NEWS_BEHAVIOUR is explicitly configured as send_empty."""

    @pytest.mark.asyncio
    async def test_handle_no_news_send_empty_builds_notice(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "send_empty")
        state = _make_sample_state(important_events=[])

        result = await handle_no_news(state)
        assert result["email_subject"] != ""
        assert "No Significant News" in result["email_subject"]
        assert "No significant" in result["email_plain"]

    def test_route_after_no_news_routes_to_format_email_when_send_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "no_news_behaviour", "send_empty")
        state = _make_sample_state(important_events=[])
        assert route_after_no_news(state) == "format_email"
