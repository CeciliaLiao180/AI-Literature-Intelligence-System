from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


RESULT_PACKAGE_VERSION = "1.0"
PROMPT_KEY = "paper_relevance"
PROMPT_VERSION = "1.0.0"
OUTPUT_SCHEMA_VERSION = "1.0"
OUTPUT_FIELDS = {
    "schema_version",
    "decision",
    "relevance_score",
    "matched_domain_codes",
    "reason",
    "confidence",
    "warnings",
    "needs_human_review",
}
DECISIONS = {"relevant", "irrelevant", "uncertain"}


class ScreeningValidationError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_score(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScreeningValidationError(message)


def _validate_output(
    output: object,
    *,
    paper_task_id: str,
    active_domain_codes: set[str],
) -> None:
    _require(isinstance(output, dict), f"{paper_task_id}: output must be an object")
    assert isinstance(output, dict)
    _require(
        set(output) == OUTPUT_FIELDS,
        f"{paper_task_id}: output fields do not match paper_relevance schema",
    )
    _require(
        output["schema_version"] == OUTPUT_SCHEMA_VERSION,
        f"{paper_task_id}: invalid schema_version",
    )
    decision = output["decision"]
    _require(decision in DECISIONS, f"{paper_task_id}: invalid decision")
    _require(
        _is_score(output["relevance_score"]),
        f"{paper_task_id}: relevance_score must be between 0 and 1",
    )
    _require(
        _is_score(output["confidence"]),
        f"{paper_task_id}: confidence must be between 0 and 1",
    )

    matched = output["matched_domain_codes"]
    _require(
        isinstance(matched, list) and all(isinstance(code, str) for code in matched),
        f"{paper_task_id}: matched_domain_codes must be a string list",
    )
    _require(len(matched) == len(set(matched)), f"{paper_task_id}: duplicate domain code")
    _require(
        set(matched).issubset(active_domain_codes),
        f"{paper_task_id}: matched_domain_codes contains an inactive or unknown code",
    )
    if decision == "relevant":
        _require(bool(matched), f"{paper_task_id}: relevant decision requires a domain")
    if decision == "irrelevant":
        _require(not matched, f"{paper_task_id}: irrelevant decision cannot match a domain")

    reason = output["reason"]
    _require(
        isinstance(reason, str) and bool(reason.strip()) and len(reason) <= 500,
        f"{paper_task_id}: reason must be 1-500 characters",
    )
    warnings = output["warnings"]
    _require(
        isinstance(warnings, list)
        and all(isinstance(item, str) and len(item) <= 300 for item in warnings),
        f"{paper_task_id}: warnings must be strings up to 300 characters",
    )
    _require(
        isinstance(output["needs_human_review"], bool),
        f"{paper_task_id}: needs_human_review must be boolean",
    )
    if decision == "uncertain":
        _require(
            output["needs_human_review"] is True,
            f"{paper_task_id}: uncertain decision requires human review",
        )


def validate_screening_results(
    task_package_path: Path,
    results_path: Path,
) -> dict[str, Any]:
    task_package = json.loads(task_package_path.read_text(encoding="utf-8"))
    results_package = json.loads(results_path.read_text(encoding="utf-8"))

    _require(
        results_package.get("result_package_version") == RESULT_PACKAGE_VERSION,
        "invalid result_package_version",
    )
    source = results_package.get("source_task_package")
    _require(isinstance(source, dict), "source_task_package must be an object")
    _require(
        source.get("sha256") == file_sha256(task_package_path),
        "source task package SHA-256 does not match",
    )
    _require(
        source.get("batch_id") == task_package.get("batch", {}).get("batch_id"),
        "source batch_id does not match",
    )
    prompt = results_package.get("prompt")
    _require(isinstance(prompt, dict), "prompt must be an object")
    _require(prompt.get("key") == PROMPT_KEY, "invalid prompt key")
    _require(prompt.get("version") == PROMPT_VERSION, "invalid prompt version")
    _require(
        prompt.get("output_schema_version") == OUTPUT_SCHEMA_VERSION,
        "invalid output schema version",
    )
    _require(
        prompt.get("prompt_spec_sha256") == task_package.get("prompt_spec", {}).get("sha256"),
        "prompt spec SHA-256 does not match task package",
    )

    task_rows = task_package.get("tasks")
    result_rows = results_package.get("results")
    _require(isinstance(task_rows, list), "task package tasks must be a list")
    _require(isinstance(result_rows, list), "results must be a list")
    tasks_by_id = {row["paper_task_id"]: row for row in task_rows}
    _require(
        len(tasks_by_id) == len(task_rows),
        "task package contains duplicate paper_task_id values",
    )
    result_ids = [row.get("paper_task_id") for row in result_rows if isinstance(row, dict)]
    _require(len(result_ids) == len(result_rows), "each result must be an object")
    _require(len(result_ids) == len(set(result_ids)), "results contain duplicate paper_task_id")
    _require(set(result_ids) == set(tasks_by_id), "results must match every task exactly once")

    decisions: Counter[str] = Counter()
    review_count = 0
    for row in result_rows:
        task_id = row["paper_task_id"]
        task = tasks_by_id[task_id]
        input_data = task["input_data"]
        _require(row.get("paper_id") == input_data["paper"]["paper_id"], f"{task_id}: paper_id mismatch")
        _require(
            row.get("pmid") == input_data["paper"]["identifiers"].get("pmid"),
            f"{task_id}: PMID mismatch",
        )
        _require(
            row.get("input_sha256") == task["input_sha256"],
            f"{task_id}: input SHA-256 mismatch",
        )
        active_codes = {
            domain["code"] for domain in input_data["domain_context"]["active_domains"]
        }
        _validate_output(
            row.get("output"),
            paper_task_id=task_id,
            active_domain_codes=active_codes,
        )
        output = row["output"]
        decisions[output["decision"]] += 1
        review_count += int(output["needs_human_review"])

    expected_summary = {
        "total": len(result_rows),
        "relevant": decisions["relevant"],
        "irrelevant": decisions["irrelevant"],
        "uncertain": decisions["uncertain"],
        "needs_human_review": review_count,
    }
    _require(
        results_package.get("summary") == expected_summary,
        "summary does not match validated results",
    )
    return {
        "valid": True,
        "batch_id": source["batch_id"],
        "prompt_key": PROMPT_KEY,
        "prompt_version": PROMPT_VERSION,
        "summary": expected_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a manual Codex Screening result package.")
    parser.add_argument("--task-package", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    report = validate_screening_results(args.task_package, args.results)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
