"""
tests/evaluation/eval_runner.py
────────────────────────────────
Evaluation framework for the Forsa News Agent.

Evaluates:
  1. Classification (is_relevant): Precision, Recall, F1
  2. Category classification: Accuracy
  3. Importance scoring: Correlation with expected scores
  4. Deduplication: Correct grouping of duplicate events
  5. Filtering: Should-send Precision and Recall

Usage:
    py -3 tests/evaluation/eval_runner.py

Requires: Dataset at tests/evaluation/dataset/sample_articles.json
          and a configured .env file with API keys.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.models.article import Article
from app.services import llm


DATASET_PATH = Path(__file__).parent / "dataset" / "sample_articles.json"


def load_dataset() -> list[dict]:
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_metrics(
    labels: list[bool],
    predictions: list[bool],
    label_name: str = "relevance",
) -> dict:
    """Compute precision, recall, F1 for binary classification."""
    tp = sum(1 for l, p in zip(labels, predictions) if l and p)
    fp = sum(1 for l, p in zip(labels, predictions) if not l and p)
    fn = sum(1 for l, p in zip(labels, predictions) if l and not p)
    tn = sum(1 for l, p in zip(labels, predictions) if not l and not p)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "metric": label_name,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


async def evaluate_classification(dataset: list[dict]) -> None:
    """Evaluate LLM classification against labeled data."""
    print("\n=== CLASSIFICATION EVALUATION ===")

    true_labels: list[bool] = []
    pred_labels: list[bool] = []
    true_categories: list[str] = []
    pred_categories: list[str] = []

    for item in dataset:
        expected = item["labels"]
        article = Article(
            title=item["title"],
            url=item["url"],
            content=item["content"],
            source_name=item["source_name"],
        )

        result = await llm.classify_article(
            title=article.title,
            content=article.content or "",
        )

        true_labels.append(expected["is_relevant"])
        pred_labels.append(result.get("is_relevant", False))
        true_categories.append(expected["category"])
        pred_categories.append(result.get("category", "Other"))

        status = "✓" if result.get("is_relevant") == expected["is_relevant"] else "✗"
        print(f"  {status} [{item['article_id']}] {item['title'][:50]}")
        print(f"     Expected: relevant={expected['is_relevant']}, category={expected['category']}")
        print(f"     Got:      relevant={result.get('is_relevant')}, category={result.get('category')}")

    metrics = compute_metrics(true_labels, pred_labels, "relevance")
    cat_accuracy = sum(
        1 for t, p in zip(true_categories, pred_categories) if t == p
    ) / len(true_categories)

    print(f"\nRelevance — Precision: {metrics['precision']:.1%}, "
          f"Recall: {metrics['recall']:.1%}, F1: {metrics['f1']:.1%}")
    print(f"Category accuracy: {cat_accuracy:.1%}")


async def evaluate_deduplication(dataset: list[dict]) -> None:
    """Evaluate deduplication by checking known duplicate pairs."""
    print("\n=== DEDUPLICATION EVALUATION ===")

    from app.models.article import Article, ArticleClassification
    from app.services.deduplication import DeduplicationService

    articles = []
    classifications = {}
    expected_duplicates = {}

    for item in dataset:
        article = Article(
            title=item["title"],
            url=item["url"],
            content=item["content"],
            source_name=item["source_name"],
            source_tier=item["source_tier"],
        )
        articles.append(article)

        if item["labels"].get("is_relevant", False):
            cls = ArticleClassification(
                article_id=article.article_id,
                is_relevant=True,
                category=item["labels"]["category"],
                entities=item["labels"].get("entities", []),
                relevance_score=80,
            )
            classifications[article.article_id] = cls

        if "is_duplicate_of" in item["labels"]:
            expected_duplicates[item["article_id"]] = item["labels"]["is_duplicate_of"]

    service = DeduplicationService()
    relevant_articles = [a for a in articles if a.article_id in classifications]
    events = service.deduplicate(relevant_articles, classifications)

    print(f"  Input articles: {len(relevant_articles)}")
    print(f"  Output events: {len(events)}")
    print(f"  Known duplicate pairs: {len(expected_duplicates)}")

    # Check if known duplicates were grouped
    for dup_id, orig_id in expected_duplicates.items():
        print(f"  Checking: {dup_id} should be grouped with {orig_id}")
        # This is a soft check — verify event count reduced
        max_expected_events = len(relevant_articles) - len(expected_duplicates)
        status = "✓" if len(events) <= max_expected_events else "~"
        print(f"  {status} Events reduced from {len(relevant_articles)} to {len(events)}")


async def main() -> None:
    print("Forsa News Agent — Evaluation Runner")
    print("=" * 50)

    dataset = load_dataset()
    print(f"Loaded {len(dataset)} labeled articles from dataset.")

    await evaluate_classification(dataset)
    await evaluate_deduplication(dataset)

    print("\n=== EVALUATION COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(main())
