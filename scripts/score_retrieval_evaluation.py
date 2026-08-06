#!/usr/bin/env python3
"""Score manually graded retrieval candidates with Recall@K and nDCG@K."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    candidate_data = json.loads(args.candidates.read_text(encoding="utf-8"))
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    with args.annotations.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            article_id = (row.get("article_id") or "").strip()
            grade_value = (row.get("relevance_grade") or "").strip()
            if not article_id or not grade_value:
                continue
            grade = int(grade_value)
            if grade not in {0, 1, 2}:
                raise ValueError(f"Invalid relevance grade for {row.get('question_id')}: {grade}")
            labels[row["question_id"]][article_id] = grade

    per_question = []
    for item in candidate_data["candidates"]:
        question_id = item["question_id"]
        annotated = labels.get(question_id, {})
        if not annotated:
            continue
        retrieved = [result["article_id"] for result in item["retrieval"]["results"][: args.k]]
        relevant = {article_id for article_id, grade in annotated.items() if grade > 0}
        retrieved_relevant = [article_id for article_id in retrieved if annotated.get(article_id, 0) > 0]
        recall = len(set(retrieved_relevant)) / len(relevant) if relevant else None
        observed_grades = [annotated.get(article_id, 0) for article_id in retrieved]
        ideal_grades = sorted(annotated.values(), reverse=True)[: args.k]
        ideal = dcg(ideal_grades)
        ndcg = dcg(observed_grades) / ideal if ideal else None
        per_question.append({"question_id": question_id, "recall_at_k": recall, "ndcg_at_k": ndcg})

    scored_recall = [row["recall_at_k"] for row in per_question if row["recall_at_k"] is not None]
    scored_ndcg = [row["ndcg_at_k"] for row in per_question if row["ndcg_at_k"] is not None]
    if not scored_recall:
        raise ValueError("No graded relevant documents found; complete the annotation CSV first.")
    result = {
        "schema_version": "retrieval-score.v1",
        "candidate_snapshot": str(args.candidates),
        "annotation_file": str(args.annotations),
        "k": args.k,
        "scored_questions": len(per_question),
        "recall_at_k": sum(scored_recall) / len(scored_recall),
        "ndcg_at_k": sum(scored_ndcg) / len(scored_ndcg),
        "per_question": per_question,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("scored_questions", "recall_at_k", "ndcg_at_k")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())