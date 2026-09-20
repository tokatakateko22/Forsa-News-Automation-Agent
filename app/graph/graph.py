"""
app/graph/graph.py
───────────────────
LangGraph StateGraph definition for the Forsa News Agent pipeline.

Graph topology:
  START
    ↓
  collect_news
    ↓
  preprocess_news
    ↓
  classify_articles
    ↓
  deduplicate_articles
    ↓
  verify_sources
    ↓
  filter_important_news ──→ [no_news] → handle_no_news → format_email → send_email → END
    ↓ [has_news]
  summarize_news
    ↓
  format_email
    ↓
  send_email
    ↓
  END
"""
from __future__ import annotations

from typing import Any
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from app.graph.state import AgentState
from app.graph.nodes.collect import collect_news
from app.graph.nodes.preprocess import preprocess_news
from app.graph.nodes.classify import classify_articles
from app.graph.nodes.deduplicate import deduplicate_articles
from app.graph.nodes.verify import verify_sources
from app.graph.nodes.filter import filter_important_news, route_after_filter
from app.graph.nodes.summarize import summarize_news
from app.graph.nodes.format_email import format_email, handle_no_news, route_after_no_news
from app.graph.nodes.send_email import send_email


def build_graph() -> CompiledStateGraph:
    """
    Construct and compile the Forsa News Agent LangGraph pipeline.
    Returns a compiled graph ready to invoke.
    """
    builder = StateGraph(AgentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node("collect_news", collect_news)
    builder.add_node("preprocess_news", preprocess_news)
    builder.add_node("classify_articles", classify_articles)
    builder.add_node("deduplicate_articles", deduplicate_articles)
    builder.add_node("verify_sources", verify_sources)
    builder.add_node("filter_important_news", filter_important_news)
    builder.add_node("summarize_news", summarize_news)
    builder.add_node("format_email", format_email)
    builder.add_node("handle_no_news", handle_no_news)
    builder.add_node("send_email", send_email)

    # ── Wire edges ────────────────────────────────────────────────────────────
    builder.add_edge(START, "collect_news")
    builder.add_edge("collect_news", "preprocess_news")
    builder.add_edge("preprocess_news", "classify_articles")
    builder.add_edge("classify_articles", "deduplicate_articles")
    builder.add_edge("deduplicate_articles", "verify_sources")
    builder.add_edge("verify_sources", "filter_important_news")

    # Conditional edge after importance filtering
    builder.add_conditional_edges(
        "filter_important_news",
        route_after_filter,
        {
            "has_news": "summarize_news",
            "no_news": "handle_no_news",
        },
    )

    # Normal news delivery path
    builder.add_edge("summarize_news", "format_email")
    builder.add_edge("format_email", "send_email")
    builder.add_edge("send_email", END)

    # Conditional edge after handle_no_news:
    # - 'end': terminates workflow at END (skipping format_email and send_email)
    # - 'format_email': proceeds to format_email -> send_email for send_empty mode
    builder.add_conditional_edges(
        "handle_no_news",
        route_after_no_news,
        {
            "format_email": "format_email",
            "end": END,
        },
    )

    return builder.compile()


# Module-level singleton — import and invoke this
pipeline = build_graph()
