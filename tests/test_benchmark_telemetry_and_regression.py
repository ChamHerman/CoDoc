from __future__ import annotations

import json
from pathlib import Path
import pytest

from codoc.models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationSummary,
)
from codoc.evaluator import (
    estimate_llm_cost,
    attribute_case_failures,
    summarize_evaluations,
    compare_benchmark_summaries,
    BenchmarkDiffReport,
    evaluate_benchmark,
)
from codoc.cli import main as cli_main


def test_estimate_llm_cost() -> None:
    # 1,000,000 prompt tokens + 1,000,000 completion tokens
    cost = estimate_llm_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000, model="gpt-4o-mini")
    # $0.15 for prompt + $0.60 for completion = $0.75
    assert cost == pytest.approx(0.75, abs=0.01)


def test_failure_attribution_diagnostics() -> None:
    case = EvaluationCase(
        case_id="case_fp_fn",
        repo_ref="fastapi",
        category="param_rename",
        expected_stale_sections=("docs/api.md::sec_expected",),
    )
    predicted_sections = {"docs/api.md::sec_unexpected"}

    attributions = attribute_case_failures(
        case=case,
        predicted_sections=predicted_sections,
        context_hints={
            "missing_link_edges": ["docs/api.md::sec_expected"],
            "verifier_verdicts": {"docs/api.md::sec_unexpected": True},
        },
    )

    assert len(attributions) == 2
    types = {a["type"] for a in attributions}
    assert types == {"FP", "FN"}

    fn_attr = next(a for a in attributions if a["type"] == "FN")
    assert fn_attr["section_id"] == "docs/api.md::sec_expected"
    assert "missed_link_edge" in fn_attr["attribution"]

    fp_attr = next(a for a in attributions if a["type"] == "FP")
    assert fp_attr["section_id"] == "docs/api.md::sec_unexpected"
    assert "over_sensitive_verifier" in fp_attr["attribution"]


def test_repair_quality_and_telemetry_in_summary() -> None:
    r1 = EvaluationResult(
        case_id="c1",
        category="param_rename",
        expected_stale_sections=("sec1",),
        predicted_stale_sections=("sec1",),
        true_positives=1,
        false_positives=0,
        false_negatives=0,
        precision=1.0,
        recall=1.0,
        f1=1.0,
        repair_mode_correct=True,
        repair_validation_passed=True,
        latency_seconds=1.2,
        token_usage={"prompt_tokens": 500, "completion_tokens": 100},
        diagnostics={"detection_latency": 0.8, "repair_latency": 0.4},
    )
    r2 = EvaluationResult(
        case_id="c2",
        category="default_change",
        expected_stale_sections=("sec2",),
        predicted_stale_sections=("sec2",),
        true_positives=1,
        false_positives=0,
        false_negatives=0,
        precision=1.0,
        recall=1.0,
        f1=1.0,
        repair_mode_correct=True,
        repair_validation_passed=False,
        latency_seconds=1.8,
        token_usage={"prompt_tokens": 600, "completion_tokens": 150},
        diagnostics={"detection_latency": 1.0, "repair_latency": 0.8},
    )

    summary = summarize_evaluations([r1, r2])

    assert summary.repair_success_rate == 0.5  # 1 out of 2 passed
    assert summary.total_tokens == 1350
    assert summary.total_cases == 2
    assert summary.average_latency_seconds == pytest.approx(1.5)


def test_compare_benchmark_summaries_regression_and_improvement() -> None:
    baseline_summary = EvaluationSummary(
        total_cases=2,
        positive_cases=2,
        negative_cases=0,
        total_true_positives=1,
        total_false_positives=1,
        total_false_negatives=1,
        precision=0.5,
        recall=0.5,
        f1=0.5,
        accuracy=0.5,
        false_positive_rate=0.0,
        repair_success_rate=0.5,
        average_latency_seconds=2.0,
        total_tokens=2000,
        case_results=(
            EvaluationResult(
                case_id="c1",
                category="param_rename",
                expected_stale_sections=("s1",),
                predicted_stale_sections=("s1",),
                true_positives=1,
                false_positives=0,
                false_negatives=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
            ),
            EvaluationResult(
                case_id="c2",
                category="default_change",
                expected_stale_sections=("s2",),
                predicted_stale_sections=("wrong",),
                true_positives=0,
                false_positives=1,
                false_negatives=1,
                precision=0.0,
                recall=0.0,
                f1=0.0,
            ),
        ),
    )

    candidate_summary = EvaluationSummary(
        total_cases=2,
        positive_cases=2,
        negative_cases=0,
        total_true_positives=2,
        total_false_positives=0,
        total_false_negatives=0,
        precision=1.0,
        recall=1.0,
        f1=1.0,
        accuracy=1.0,
        false_positive_rate=0.0,
        repair_success_rate=1.0,
        average_latency_seconds=1.5,
        total_tokens=1800,
        case_results=(
            EvaluationResult(
                case_id="c1",
                category="param_rename",
                expected_stale_sections=("s1",),
                predicted_stale_sections=("s1",),
                true_positives=1,
                false_positives=0,
                false_negatives=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
            ),
            EvaluationResult(
                case_id="c2",
                category="default_change",
                expected_stale_sections=("s2",),
                predicted_stale_sections=("s2",),
                true_positives=1,
                false_positives=0,
                false_negatives=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
            ),
        ),
    )

    diff: BenchmarkDiffReport = compare_benchmark_summaries(baseline_summary, candidate_summary)

    assert diff.delta_precision == pytest.approx(0.5)
    assert diff.delta_recall == pytest.approx(0.5)
    assert diff.delta_f1 == pytest.approx(0.5)
    assert diff.delta_accuracy == pytest.approx(0.5)
    assert len(diff.improvements) == 1
    assert diff.improvements[0] == "c2"
    assert len(diff.regressions) == 0
    assert "## Benchmark Regression & Comparison Report" in diff.to_markdown()


def test_cli_diff_against(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(
        json.dumps([
            {
                "case_id": "c1",
                "repo_ref": "fastapi",
                "category": "param_rename",
                "expected_stale_sections": ["s1"],
                "expected_mode": "auto_fix",
            }
        ]),
        encoding="utf-8",
    )

    baseline_file = tmp_path / "baseline.json"
    baseline_summary = EvaluationSummary(
        total_cases=1,
        positive_cases=1,
        negative_cases=0,
        total_true_positives=0,
        total_false_positives=1,
        total_false_negatives=1,
        precision=0.0,
        recall=0.0,
        f1=0.0,
        accuracy=0.0,
        false_positive_rate=0.0,
        repair_success_rate=0.0,
        average_latency_seconds=2.0,
        total_tokens=1000,
        case_results=(
            EvaluationResult(
                case_id="c1",
                category="param_rename",
                expected_stale_sections=("s1",),
                predicted_stale_sections=("wrong",),
                true_positives=0,
                false_positives=1,
                false_negatives=1,
                precision=0.0,
                recall=0.0,
                f1=0.0,
            ),
        ),
    )
    baseline_file.write_text(json.dumps(baseline_summary.to_dict()), encoding="utf-8")

    out_md = tmp_path / "diff.md"

    code = cli_main([
        "evaluate",
        "--manifest",
        str(manifest_file),
        "--diff-against",
        str(baseline_file),
        "--output-markdown",
        str(out_md),
    ])

    assert code == 0
    assert out_md.is_file()
    content = out_md.read_text(encoding="utf-8")
    assert "Benchmark Regression & Comparison Report" in content
