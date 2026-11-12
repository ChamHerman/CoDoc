# Phase 5: Real Repository Evaluation

**Steps covered:**
- **Step 5.1:** Build benchmark fork / evaluation dataset on real open-source Python projects (e.g., FastAPI, Pydantic) with deliberate doc-invalidating commits
- **Step 5.2:** Measure detection accuracy (TP, FP, FN, precision, recall) and repair quality, and report benchmark results in README

---

## Problem Statement

Testing only on tiny synthetic toy repositories hides real-world failure modes: noisy docstrings, overloaded helper names, complex decorators, multi-file imports, and nuanced Markdown layouts. Without quantitative accuracy metrics on real open-source codebases, there is no proof that CoDoc catches actual staleness without flooding teams with false positives.

## Solution

Create a reproducible evaluation harness backed by real open-source Python repository fixtures (forked/pinned commits from projects such as FastAPI and Pydantic) containing curated `EvaluationCase` scenarios—both true doc-breaking mutations and benign refactors—to measure True Positives, False Positives, False Negatives, precision, recall, and repair quality, and publish the verified numbers in the project README.

## User Stories

1. As a repository maintainer, I want a reproducible `EvaluationCase` dataset derived from real open-source Python repositories (such as FastAPI and Pydantic), so that CoDoc is battle-tested against production-grade code and Markdown.
2. As an evaluation engineer, I want positive `EvaluationCase` scenarios covering realistic doc-breaking changes (renamed parameter, changed default value, removed CLI flag/option, altered endpoint path or status code, and changed schema field type), so that all major Phase 1–3 paths are exercised.
3. As an evaluation engineer, I want negative (control) `EvaluationCase` scenarios covering benign changes (internal variable rename, whitespace/comment edits, test additions, and PRs where docs were already updated alongside code), so that false-positive rate is measured honestly.
4. As an evaluation engineer, I want an automated evaluation runner that executes the Phase 1–3 pipeline on each `EvaluationCase` and compares detected stale `DocSection` IDs against ground-truth expected sections, so that TP, FP, and FN counts are computed deterministically.
5. As an evaluation engineer, I want the evaluation runner to record per-case metrics (latency, token usage/cost, TP, FP, FN, precision, recall, F1, and `RepairMode` routing accuracy), so that regressions can be spotted after prompt or threshold tuning.
6. As an evaluation engineer, I want repair quality scored on confirmed stale cases (whether the generated `RepairPatch` accurately reflects the new code and passes `ValidationVerdict`), so that end-to-end self-healing quality—not just detection—is quantified.
7. As a repository maintainer, I want the Action installed and verified live on a real forked open-source repository (running an actual PR triggering the Phase 4 workflow), so that GitHub Actions integration is proven end to end outside local mocks.
8. As a visitor or interviewer reading the repository README, I want a clear benchmark table showing exact dataset size, scenarios tested, precision, recall, false-positive rate, and correction quality alongside methodology notes, so that CoDoc's reliability is transparent and evidence-backed.

## Implementation Decisions

- **Step 5.1 — Benchmark Fork & `EvaluationCase` Corpus**:
  - Pin specific base commits of real OSS Python repositories (primary: `FastAPI` and/or `Pydantic`) in a reproducible evaluation manifest or test fork.
  - Define structured `EvaluationCase` schema (`case_id`, `repo_ref`, `base_commit`, `head_commit` or patch, `category` such as `param_rename` / `default_change` / `behavioral_change` / `benign_refactor` / `already_updated_docs`, `expected_stale_sections`, `expected_mode`).
  - Include at least 15–20 labeled cases spanning both positive staleness and negative control cases, plus at least one live PR run against a forked repository with CoDoc Action installed.
- **Step 5.2 — Evaluation Runner & README Reporting**:
  - Build a CLI/runner entrypoint (`evaluate_benchmark(cases, runner_config) -> EvaluationSummary`) that scores predicted stale `DocSection` sets against `expected_stale_sections` for TP, FP, FN, Precision, Recall, and Repair Accuracy.
  - Save structured JSON/Markdown report output and document exact run date, model versions, thresholds used, and caveats in the repository `README`.

## Testing Decisions

- **What makes a good test**: Separate fast deterministic unit tests of the metric calculator/harness (`TP/FP/FN/precision/recall` computation from synthetic predictions vs ground truth) from optional/live benchmark runs that require real LLM API keys (`@pytest.mark.live` or explicit CLI invocation).
- **Modules tested**: Evaluation metric aggregator, `EvaluationCase` manifest loader, and end-to-end benchmark runner seam.
- **Prior art**: Standard `pytest` suite for metric calculation; dedicated reproducible evaluation command for live OSS corpus scoring.

## Out of Scope

- Continuous automated scraping of arbitrary third-party GitHub repos on every commit.
- Fine-tuning custom embedding or LLM weights on the evaluation dataset.
- Building a web dashboard for evaluation tracking (Markdown/JSON output and README summary suffice).

## Further Notes

- Canonical vocabulary follows `CONTEXT.md` (`EvaluationCase`, `DocSection`, `StalenessVerdict`, `RepairPatch`, `RepairMode`).
- Never cherry-pick only passing scenarios in README metrics; include negative controls and document any failure modes honestly.
