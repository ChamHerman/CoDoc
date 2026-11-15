from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Sequence

from codoc.models import (
    BenchmarkDiffReport,
    EvaluationCase,
    EvaluationResult,
    EvaluationSummary,
    RepairMode,
)

__all__ = [
    "BenchmarkDiffReport",
    "attribute_case_failures",
    "compare_benchmark_summaries",
    "compute_case_metrics",
    "default_case_runner",
    "estimate_llm_cost",
    "evaluate_benchmark",
    "evaluate_case",
    "load_evaluation_manifest",
    "summarize_evaluations",
]

MODEL_COSTS_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
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


def _ensure_summary(val: EvaluationSummary | dict[str, Any]) -> EvaluationSummary:
    if isinstance(val, EvaluationSummary):
        return val
    return EvaluationSummary.from_dict(val)


def compare_benchmark_summaries(
    baseline: EvaluationSummary | dict[str, Any],
    candidate: EvaluationSummary | dict[str, Any],
) -> BenchmarkDiffReport:
    base_summary = _ensure_summary(baseline)
    cand_summary = _ensure_summary(candidate)

    delta_precision = cand_summary.precision - base_summary.precision
    delta_recall = cand_summary.recall - base_summary.recall
    delta_f1 = cand_summary.f1 - base_summary.f1
    delta_accuracy = cand_summary.accuracy - base_summary.accuracy
    delta_fpr = cand_summary.false_positive_rate - base_summary.false_positive_rate
    delta_latency = cand_summary.average_latency_seconds - base_summary.average_latency_seconds
    delta_tokens = cand_summary.total_tokens - base_summary.total_tokens

    # Compute exact cost from prompt and completion token counts when available
    if base_summary.total_prompt_tokens > 0 or base_summary.total_completion_tokens > 0:
        base_cost = estimate_llm_cost(base_summary.total_prompt_tokens, base_summary.total_completion_tokens)
    else:
        base_cost = estimate_llm_cost(base_summary.total_tokens // 2, base_summary.total_tokens // 2)

    if cand_summary.total_prompt_tokens > 0 or cand_summary.total_completion_tokens > 0:
        cand_cost = estimate_llm_cost(cand_summary.total_prompt_tokens, cand_summary.total_completion_tokens)
    else:
        cand_cost = estimate_llm_cost(cand_summary.total_tokens // 2, cand_summary.total_tokens // 2)

    delta_cost = cand_cost - base_cost

    base_cases = {r.case_id: r for r in base_summary.case_results}
    cand_cases = {r.case_id: r for r in cand_summary.case_results}

    regressions: list[str] = []
    improvements: list[str] = []

    for case_id, c_res in cand_cases.items():
        if case_id in base_cases:
            b_res = base_cases[case_id]
            b_pass = (b_res.false_positives == 0 and b_res.false_negatives == 0)
            c_pass = (c_res.false_positives == 0 and c_res.false_negatives == 0)
            if b_pass and not c_pass:
                regressions.append(case_id)
            elif not b_pass and c_pass:
                improvements.append(case_id)

    return BenchmarkDiffReport(
        baseline_summary=base_summary,
        candidate_summary=cand_summary,
        delta_precision=delta_precision,
        delta_recall=delta_recall,
        delta_f1=delta_f1,
        delta_accuracy=delta_accuracy,
        delta_false_positive_rate=delta_fpr,
        delta_latency_seconds=delta_latency,
        delta_tokens=delta_tokens,
        delta_cost_usd=delta_cost,
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
            # False alarm on clean code/docs: 0 precision, 0 recall, 0 F1
            precision = 0.0
            recall = 0.0
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
            total_prompt_tokens=0,
            total_completion_tokens=0,
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
    total_prompt_tokens = sum(r.token_usage.get("prompt_tokens", 0) for r in results)
    total_completion_tokens = sum(r.token_usage.get("completion_tokens", 0) for r in results)
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
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
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
