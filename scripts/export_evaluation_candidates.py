#!/usr/bin/env python3
"""Export reproducible hybrid-retrieval candidates for manual evaluation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def request_json(api_base_url: str, path: str, params: dict[str, str]) -> dict:
    query = urlencode(params)
    with urlopen(f"{api_base_url}{path}?{query}", timeout=30) as response:  # nosec B310
        return json.load(response)


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=Path("docs/evaluation/questions.v1.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-base-url", default="http://localhost:8000/api/v1")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    candidates = []
    for item in questions:
        params = {"query": item["question"], "limit": str(args.limit)}
        if item["topic"]:
            params["topic"] = item["topic"]
        result = request_json(args.api_base_url, "/search", params)
        candidates.append(
            {
                "question_id": item["id"],
                "question": item["question"],
                "topic": item["topic"],
                "retrieval": result,
            }
        )

    payload = {
        "schema_version": "evaluation-candidates.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "api_base_url": args.api_base_url,
        "candidate_limit": args.limit,
        "questions_file": str(args.questions),
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(candidates)} question candidates to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())