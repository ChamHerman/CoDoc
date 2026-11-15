from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Sequence

from codoc.models import (
    EvaluationCase,
    EvaluationResult,
    EvaluationSummary,
    RepairMode,
)

MODEL_COSTS_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
    "claude-3-5-sonnet": {"prompt": 3.00, "completion": 15.00},
    "claude-3-haiku": {"prompt": 0.25, "completion": 1.25},
}


def estimate_llm_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str = "gpt-4o-mini",
) -> float:
    rates = MODEL_COSTS_PER_MILLION.get(model, MODEL_COSTS_PER_MILLION["gpt-4o-mini"])
    prompt_cost = (prompt_tokens / 1_000_000.0) * rates["prompt"]
    completion_cost = (completion_tokens / 1_000_000.0) * rates["completion"]
    return prompt_cost + completion_cost


def attribute_case_failures(
    case: EvaluationCase,
    predicted_sections: Iterable[str],
    context_hints: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    hints = context_hints or {}
    expected_set = set(case.expected_stale_sections)
    predicted_set = set(predicted_sections)

    false_positives = sorted(predicted_set - expected_set)
    false_negatives = sorted(expected_set - predicted_set)

    attributions: list[dict[str, str]] = []

    for fn in false_negatives:
        missing_links = hints.get("missing_link_edges", [])
        skipped_filters = hints.get("filter_skipped", [])
        if fn in missing_links or (missing_links and any(fn == m for m in missing_links)):
            attr = "missed_link_edge"
            details = "No LinkEdge in LinkGraph associated changed symbol with this doc section."
        elif fn in skipped_filters:
            attr = "change_filter_skipped"
            details = "AST change filter classified code delta as cosmetic/skipped."
        else:
            attr = "under_sensitive_verifier"
            details = "Staleness verifier deemed suspect section accurate or confidence fell below threshold."
        attributions.append({
            "type": "FN",
            "section_id": fn,
            "attribution": attr,
            "details": details,
        })

    for fp in false_positives:
        verifier_verdicts = hints.get("verifier_verdicts", {})
        noisy_links = hints.get("noisy_embeddings", [])
        if fp in noisy_links:
            attr = "heuristic_link_noise"
            details = "Loose heuristic or embedding link attached an unrelated code change to this section."
        else:
            attr = "over_sensitive_verifier"
            details = "Staleness verifier flagged section as stale despite benign refactor or synchronized docs."
        attributions.append({
            "type": "FP",
            "section_id": fp,
            "attribution": attr,
            "details": details,
        })

    return attributions


@dataclass(frozen=True, slots=True)
class BenchmarkDiffReport:
    baseline_summary: EvaluationSummary
    candidate_summary: EvaluationSummary
    delta_precision: float
    delta_recall: float
    delta_f1: float
    delta_accuracy: float
    delta_false_positive_rate: float
    delta_latency_seconds: float
    delta_tokens: int
    delta_cost_usd: float
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline_summary.to_dict(),
            "candidate": self.candidate_summary.to_dict(),
            "delta_precision": self.delta_precision,
            "delta_recall": self.delta_recall,
            "delta_f1": self.delta_f1,
            "delta_accuracy": self.delta_accuracy,
            "delta_false_positive_rate": self.delta_false_positive_rate,
            "delta_latency_seconds": self.delta_latency_seconds,
            "delta_tokens": self.delta_tokens,
            "delta_cost_usd": self.delta_cost_usd,
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
        }

    def to_markdown(self) -> str:
        def _delta_badge(delta: float, higher_is_better: bool = True) -> str:
            if abs(delta) < 1e-4:
                return "⚪ Neutral"
            is_good = (delta > 0) if higher_is_better else (delta < 0)
            sign = "+" if delta > 0 else ""
            prefix = "🟢" if is_good else "🔴"
            return f"{prefix} {sign}{delta:.1%}"

        lines = [
            "## Benchmark Regression & Comparison Report",
            "",
            "| Metric | Baseline | Candidate | Delta |",
            "| :--- | :--- | :--- | :--- |",
            f"| **Precision** | {self.baseline_summary.precision:.1%} | {self.candidate_summary.precision:.1%} | {_delta_badge(self.delta_precision, True)} |",
            f"| **Recall** | {self.baseline_summary.recall:.1%} | {self.candidate_summary.recall:.1%} | {_delta_badge(self.delta_recall, True)} |",
            f"| **F1 Score** | {self.baseline_summary.f1:.1%} | {self.candidate_summary.f1:.1%} | {_delta_badge(self.delta_f1, True)} |",
            f"| **Exact Match Accuracy** | {self.baseline_summary.accuracy:.1%} | {self.candidate_summary.accuracy:.1%} | {_delta_badge(self.delta_accuracy, True)} |",
            f"| False Positive Rate | {self.baseline_summary.false_positive_rate:.1%} | {self.candidate_summary.false_positive_rate:.1%} | {_delta_badge(self.delta_false_positive_rate, False)} |",
            f"| Avg Latency | {self.baseline_summary.average_latency_seconds:.2f}s | {self.candidate_summary.average_latency_seconds:.2f}s | {self.delta_latency_seconds:+.2f}s |",
            f"| Total Tokens | {self.baseline_summary.total_tokens} | {self.candidate_summary.total_tokens} | {self.delta_tokens:+d} |",
            f"| Estimated Cost | ${estimate_llm_cost(self.baseline_summary.total_tokens // 2, self.baseline_summary.total_tokens // 2):.4f} | ${estimate_llm_cost(self.candidate_summary.total_tokens // 2, self.candidate_summary.total_tokens // 2):.4f} | ${self.delta_cost_usd:+.4f} |",
            "",
        ]

        if self.regressions:
            lines.append(f"### ⚠️ Regressions ({len(self.regressions)})\n")
            for reg in self.regressions:
                lines.append(f"- 🔴 `{reg}`: Regression detected against baseline.")
            lines.append("")
        else:
            lines.append("### ✅ Regressions (0)\nNo accuracy regressions detected.\n")

        if self.improvements:
            lines.append(f"### 🚀 Improvements ({len(self.improvements)})\n")
            for imp in self.improvements:
                lines.append(f"- 🟢 `{imp}`: Passed in candidate after failing in baseline.")
            lines.append("")
        else:
            lines.append("### Improvements (0)\n")

        return "\n".join(lines)


def _ensure_summary(val: EvaluationSummary | dict[str, Any]) -> EvaluationSummary:
    if isinstance(val, EvaluationSummary):
        return val
    case_results = [
        EvaluationResult(
            case_id=r["case_id"],
            category=r.get("category", "general"),
            expected_stale_sections=tuple(r.get("expected_stale_sections", ())),
            predicted_stale_sections=tuple(r.get("predicted_stale_sections", ())),
            true_positives=r.get("true_positives", 0),
            false_positives=r.get("false_positives", 0),
            false_negatives=r.get("false_negatives", 0),
            precision=r.get("precision", 0.0),
            recall=r.get("recall", 0.0),
            f1=r.get("f1", 0.0),
            repair_mode_correct=r.get("repair_mode_correct"),
            repair_validation_passed=r.get("repair_validation_passed"),
            latency_seconds=r.get("latency_seconds", 0.0),
            token_usage=r.get("token_usage", {}),
            diagnostics=r.get("diagnostics", {}),
            error=r.get("error"),
        )
        for r in val.get("case_results", [])
    ]
    return EvaluationSummary(
        total_cases=val.get("total_cases", len(case_results)),
        positive_cases=val.get("positive_cases", 0),
        negative_cases=val.get("negative_cases", 0),
        total_true_positives=val.get("total_true_positives", 0),
        total_false_positives=val.get("total_false_positives", 0),
        total_false_negatives=val.get("total_false_negatives", 0),
        precision=val.get("precision", 0.0),
        recall=val.get("recall", 0.0),
        f1=val.get("f1", 0.0),
        accuracy=val.get("accuracy", 0.0),
        false_positive_rate=val.get("false_positive_rate", 0.0),
        repair_success_rate=val.get("repair_success_rate", 0.0),
        average_latency_seconds=val.get("average_latency_seconds", 0.0),
        total_tokens=val.get("total_tokens", 0),
        case_results=tuple(case_results),
    )


def compare_benchmark_summaries(
    baseline: EvaluationSummary | dict[str, Any],
    candidate: EvaluationSummary | dict[str, Any],
) -> BenchmarkDiffReport:
    b = _ensure_summary(baseline)
    c = _ensure_summary(candidate)

    d_prec = c.precision - b.precision
    d_rec = c.recall - b.recall
    d_f1 = c.f1 - b.f1
    d_acc = c.accuracy - b.accuracy
    d_fpr = c.false_positive_rate - b.false_positive_rate
    d_lat = c.average_latency_seconds - b.average_latency_seconds
    d_tok = c.total_tokens - b.total_tokens

    b_cost = estimate_llm_cost(b.total_tokens // 2, b.total_tokens // 2)
    c_cost = estimate_llm_cost(c.total_tokens // 2, c.total_tokens // 2)
    d_cost = c_cost - b_cost

    b_cases = {r.case_id: r for r in b.case_results}
    c_cases = {r.case_id: r for r in c.case_results}

    regressions: list[str] = []
    improvements: list[str] = []

    for case_id, c_res in c_cases.items():
        if case_id in b_cases:
            b_res = b_cases[case_id]
            b_pass = (b_res.false_positives == 0 and b_res.false_negatives == 0)
            c_pass = (c_res.false_positives == 0 and c_res.false_negatives == 0)
            if b_pass and not c_pass:
                regressions.append(case_id)
            elif not b_pass and c_pass:
                improvements.append(case_id)

    return BenchmarkDiffReport(
        baseline_summary=b,
        candidate_summary=c,
        delta_precision=d_prec,
        delta_recall=d_rec,
        delta_f1=d_f1,
        delta_accuracy=d_acc,
        delta_false_positive_rate=d_fpr,
        delta_latency_seconds=d_lat,
        delta_tokens=d_tok,
        delta_cost_usd=d_cost,
        regressions=tuple(regressions),
        improvements=tuple(improvements),
    )


def compute_case_metrics(
    expected: Iterable[str],
    predicted: Iterable[str],
) -> tuple[int, int, int, float, float, float]:
    """Compute (true_positives, false_positives, false_negatives, precision, recall, f1)

    Handles zero-division edge cases and control (negative) scenarios gracefully.
    """
    exp_set = set(expected)
    pred_set = set(predicted)

    tp = len(exp_set & pred_set)
    fp = len(pred_set - exp_set)
    fn = len(exp_set - pred_set)

    if len(exp_set) == 0:
        if len(pred_set) == 0:
            # Clean negative control: no expected stale sections, none predicted
            precision = 1.0
            recall = 1.0
            f1 = 1.0
        else:
            # False alarm on clean code/docs
            precision = 0.0
            recall = 1.0
            f1 = 0.0
    else:
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return tp, fp, fn, precision, recall, f1


def summarize_evaluations(results: Sequence[EvaluationResult]) -> EvaluationSummary:
    total_cases = len(results)
    if total_cases == 0:
        return EvaluationSummary(
            total_cases=0,
            positive_cases=0,
            negative_cases=0,
            total_true_positives=0,
            total_false_positives=0,
            total_false_negatives=0,
            precision=1.0,
            recall=1.0,
            f1=1.0,
            accuracy=1.0,
            false_positive_rate=0.0,
            repair_success_rate=0.0,
            average_latency_seconds=0.0,
            total_tokens=0,
            case_results=(),
        )

    pos_cases = sum(1 for r in results if len(r.expected_stale_sections) > 0)
    neg_cases = total_cases - pos_cases

    tot_tp = sum(r.true_positives for r in results)
    tot_fp = sum(r.false_positives for r in results)
    tot_fn = sum(r.false_negatives for r in results)

    # Global micro-averaged precision and recall
    if tot_tp + tot_fp > 0:
        precision = tot_tp / (tot_tp + tot_fp)
    else:
        precision = 1.0 if tot_fn == 0 else 0.0

    if tot_tp + tot_fn > 0:
        recall = tot_tp / (tot_tp + tot_fn)
    else:
        recall = 1.0 if tot_fp == 0 else 0.0

    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    # Exact match accuracy (no false positives and no false negatives)
    exact_matches = sum(1 for r in results if r.false_positives == 0 and r.false_negatives == 0)
    accuracy = exact_matches / total_cases

    # False positive rate on negative control cases
    negative_results = [r for r in results if len(r.expected_stale_sections) == 0]
    if negative_results:
        fp_cases = sum(1 for r in negative_results if r.false_positives > 0)
        false_positive_rate = fp_cases / len(negative_results)
    else:
        false_positive_rate = 0.0

    # Repair success rate
    evaluated_repairs = [r for r in results if r.repair_validation_passed is not None]
    if evaluated_repairs:
        successful_repairs = sum(1 for r in evaluated_repairs if r.repair_validation_passed)
        repair_success_rate = successful_repairs / len(evaluated_repairs)
    else:
        repair_success_rate = 0.0

    avg_latency = sum(r.latency_seconds for r in results) / total_cases
    total_tokens = sum(sum(r.token_usage.values()) for r in results)

    return EvaluationSummary(
        total_cases=total_cases,
        positive_cases=pos_cases,
        negative_cases=neg_cases,
        total_true_positives=tot_tp,
        total_false_positives=tot_fp,
        total_false_negatives=tot_fn,
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=accuracy,
        false_positive_rate=false_positive_rate,
        repair_success_rate=repair_success_rate,
        average_latency_seconds=avg_latency,
        total_tokens=total_tokens,
        case_results=tuple(results),
    )


def load_evaluation_manifest(manifest_path: Path | str) -> list[EvaluationCase]:
    path = Path(manifest_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_cases: list[dict[str, Any]]
    if isinstance(data, list):
        raw_cases = data
    elif isinstance(data, dict) and "cases" in data:
        raw_cases = data["cases"]
    else:
        raise ValueError(f"Manifest at {manifest_path} must be a list or object with 'cases' key")

    return [EvaluationCase.from_dict(item) for item in raw_cases]


PipelineRunnerFn = Callable[
    [EvaluationCase],
    tuple[set[str], RepairMode | None, bool | None, dict[str, Any]],
]


def default_case_runner(
    case: EvaluationCase,
) -> tuple[set[str], RepairMode | None, bool | None, dict[str, Any]]:
    """Default offline stub runner predicting based on metadata if provided."""
    predicted_sections = set(case.metadata.get("mock_predicted_sections", case.expected_stale_sections))
    predicted_mode = case.metadata.get("mock_predicted_mode", case.expected_mode)
    validation_passed = case.metadata.get("mock_validation_passed", True if case.expected_mode else None)
    diagnostics = case.metadata.get("mock_diagnostics", {})
    return predicted_sections, predicted_mode, validation_passed, diagnostics


def evaluate_case(
    case: EvaluationCase,
    runner_fn: PipelineRunnerFn | None = None,
) -> EvaluationResult:
    runner = runner_fn or default_case_runner
    start_time = time.perf_counter()
    error_msg: str | None = None
    predicted_sections: set[str] = set()
    predicted_mode: RepairMode | None = None
    validation_passed: bool | None = None
    diagnostics: dict[str, Any] = {}

    try:
        predicted_sections, predicted_mode, validation_passed, diagnostics = runner(case)
    except Exception as exc:
        error_msg = str(exc)

    elapsed = time.perf_counter() - start_time

    tp, fp, fn, prec, rec, f1 = compute_case_metrics(
        case.expected_stale_sections,
        predicted_sections,
    )

    if fp > 0 or fn > 0:
        diagnostics["failure_attributions"] = attribute_case_failures(
            case,
            predicted_sections,
            context_hints=diagnostics,
        )

    mode_correct = (predicted_mode == case.expected_mode) if case.expected_mode is not None else None
    token_usage = diagnostics.get("token_usage", {})

    return EvaluationResult(
        case_id=case.case_id,
        category=case.category,
        expected_stale_sections=case.expected_stale_sections,
        predicted_stale_sections=tuple(sorted(predicted_sections)),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=prec,
        recall=rec,
        f1=f1,
        repair_mode_correct=mode_correct,
        repair_validation_passed=validation_passed,
        latency_seconds=elapsed,
        token_usage=token_usage,
        diagnostics=diagnostics,
        error=error_msg,
    )


def evaluate_benchmark(
    cases: Sequence[EvaluationCase],
    runner_fn: PipelineRunnerFn | None = None,
) -> EvaluationSummary:
    results: list[EvaluationResult] = []
    for case in cases:
        result = evaluate_case(case, runner_fn=runner_fn)
        results.append(result)
    return summarize_evaluations(results)
