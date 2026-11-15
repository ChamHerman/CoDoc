from __future__ import annotations

import json
from pathlib import Path
import pytest

from codoc.diff_parser import parse_unified_diff
from codoc.evaluator import (
    evaluate_benchmark,
    load_evaluation_manifest,
)
from codoc.models import EvaluationCase


CORPUS_MANIFEST_PATH = Path("benchmarks/manifest.json")


def test_corpus_manifest_file_exists_and_size() -> None:
    assert CORPUS_MANIFEST_PATH.is_file(), f"Manifest expected at {CORPUS_MANIFEST_PATH}"
    cases = load_evaluation_manifest(CORPUS_MANIFEST_PATH)
    assert 15 <= len(cases) <= 20, f"Expected 15-20 cases, got {len(cases)}"


def test_corpus_case_schema_and_patch_validity() -> None:
    cases = load_evaluation_manifest(CORPUS_MANIFEST_PATH)
    case_ids = [c.case_id for c in cases]
    assert len(case_ids) == len(set(case_ids)), "Duplicate case_id found in corpus manifest"

    categories = {c.category for c in cases}
    expected_categories = {
        "param_rename",
        "default_change",
        "endpoint_change",
        "schema_change",
        "cli_option_change",
        "benign_refactor",
        "already_updated_docs",
    }
    assert expected_categories.issubset(categories)

    for case in cases:
        assert case.case_id, "case_id cannot be empty"
        assert case.repo_ref in ("fastapi", "pydantic"), f"Unexpected repo_ref: {case.repo_ref}"
        assert case.category, "category cannot be empty"
        assert case.patch.strip(), f"Case {case.case_id} has empty patch"

        # Ensure unified diff parses cleanly
        hunks = parse_unified_diff(case.patch)
        assert len(hunks) > 0, f"Case {case.case_id} patch produced 0 hunks"

        # Check negative vs positive expectations
        if case.category in ("benign_refactor", "already_updated_docs"):
            assert len(case.expected_stale_sections) == 0, (
                f"Negative control case {case.case_id} must have 0 expected stale sections"
            )
            assert case.expected_mode is None
        else:
            assert len(case.expected_stale_sections) > 0, (
                f"Positive stale case {case.case_id} must have >= 1 expected stale sections"
            )
            assert case.expected_mode in ("auto_fix", "review_flag")


def test_run_benchmark_on_curated_corpus() -> None:
    cases = load_evaluation_manifest(CORPUS_MANIFEST_PATH)
    summary = evaluate_benchmark(cases)

    assert summary.total_cases == len(cases)
    assert summary.positive_cases >= 10
    assert summary.negative_cases >= 5
    assert summary.accuracy >= 0.90
    assert summary.precision >= 0.90
    assert summary.recall >= 0.90
    assert summary.false_positive_rate <= 0.10
