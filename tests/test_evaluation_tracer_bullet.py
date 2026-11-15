from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from codoc.models import EvaluationCase, EvaluationResult, EvaluationSummary, RepairMode
from codoc.evaluator import (
    compute_case_metrics,
    summarize_evaluations,
    load_evaluation_manifest,
    evaluate_case,
    evaluate_benchmark,
)
from codoc.cli import main as cli_main


def test_compute_case_metrics_positive_stale_case() -> None:
    expected = {"docs/api.md::users", "docs/api.md::auth"}
    predicted = {"docs/api.md::users", "docs/api.md::other"}

    tp, fp, fn, prec, rec, f1 = compute_case_metrics(expected, predicted)

    assert tp == 1  # users
    assert fp == 1  # other
    assert fn == 1  # auth
    assert prec == pytest.approx(0.5)
    assert rec == pytest.approx(0.5)
    assert f1 == pytest.approx(0.5)


def test_compute_case_metrics_negative_control_clean() -> None:
    expected: set[str] = set()
    predicted: set[str] = set()

    tp, fp, fn, prec, rec, f1 = compute_case_metrics(expected, predicted)

    assert tp == 0
    assert fp == 0
    assert fn == 0
    assert prec == 1.0
    assert rec == 1.0
    assert f1 == 1.0


def test_compute_case_metrics_negative_control_false_positive() -> None:
    expected: set[str] = set()
    predicted = {"docs/api.md::wrong"}

    tp, fp, fn, prec, rec, f1 = compute_case_metrics(expected, predicted)

    assert tp == 0
    assert fp == 1
    assert fn == 0
    assert prec == 0.0
    assert rec == 1.0
    assert f1 == 0.0


def test_evaluation_case_model_roundtrip() -> None:
    case = EvaluationCase(
        case_id="case_01",
        repo_ref="fastapi",
        category="param_rename",
        description="Renamed foo to bar",
        expected_stale_sections=("docs/tutorial.md::endpoint",),
        expected_mode="auto_fix",
    )
    d = case.to_dict()
    reconstituted = EvaluationCase.from_dict(d)
    assert reconstituted == case


def test_load_evaluation_manifest_and_run_benchmark(tmp_path: Path) -> None:
    manifest_data = {
        "benchmark": "tracer_bullet",
        "cases": [
            {
                "case_id": "case_pos",
                "repo_ref": "test_repo",
                "category": "param_rename",
                "expected_stale_sections": ["docs/a.md::sec1"],
                "expected_mode": "auto_fix",
            },
            {
                "case_id": "case_neg",
                "repo_ref": "test_repo",
                "category": "benign_refactor",
                "expected_stale_sections": [],
                "expected_mode": None,
            },
        ],
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    cases = load_evaluation_manifest(manifest_file)
    assert len(cases) == 2

    # Mock runner that perfectly predicts both
    def mock_runner(case: EvaluationCase) -> tuple[set[str], RepairMode | None, bool | None, dict[str, Any]]:
        if case.case_id == "case_pos":
            return {"docs/a.md::sec1"}, "auto_fix", True, {}
        return set(), None, None, {}

    summary = evaluate_benchmark(cases, runner_fn=mock_runner)
    assert summary.total_cases == 2
    assert summary.positive_cases == 1
    assert summary.negative_cases == 1
    assert summary.total_true_positives == 1
    assert summary.total_false_positives == 0
    assert summary.total_false_negatives == 0
    assert summary.precision == 1.0
    assert summary.recall == 1.0
    assert summary.f1 == 1.0
    assert summary.accuracy == 1.0
    assert summary.false_positive_rate == 0.0
    assert "# CoDoc Evaluation Benchmark Summary" in summary.to_markdown()


def test_cli_codoc_evaluate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_data = [
        {
            "case_id": "cli_case_1",
            "repo_ref": "test_repo",
            "category": "default_change",
            "expected_stale_sections": ["docs/config.md::timeout"],
        }
    ]
    manifest_file = tmp_path / "eval_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"

    code = cli_main([
        "evaluate",
        "--manifest",
        str(manifest_file),
        "--output-json",
        str(out_json),
        "--output-markdown",
        str(out_md),
    ])

    assert code == 0
    assert out_json.is_file()
    assert out_md.is_file()

    report_obj = json.loads(out_json.read_text(encoding="utf-8"))
    assert report_obj["total_cases"] == 1
