# Phase 2: Change Detection Pipeline

**Steps covered:**
- **Step 2.1:** Parse unified git diff and pair `base` and `head` `CodeChunk` revisions into `CodeDelta` records
- **Step 2.2:** Filter for meaningful code changes (`ChangeFilterResult`) using AST-normalized structural comparison
- **Step 2.3:** Identify affected `SuspectSection` entries via `LinkGraph` and resolve doc content at `head`
- **Step 2.4:** Verify staleness with an LLM (`StalenessVerdict`) via provider-agnostic adapter

---

## Problem Statement

Checking every documentation section on every pull request is slow, expensive, and noisy, while raw line-diff heuristics either miss behavioral code changes or flood reviewers with false alarms caused by formatting, comments, test edits, or documentation already updated in the same pull request.

## Solution

Build a change-detection pipeline that parses pull request diffs across both `base` and `head` revisions to construct `CodeDelta` pairs (including additions, modifications, renames, and deletions), deterministically filters out non-behavioral changes via AST normalization, queries the Phase 1 `LinkGraph` for linked `SuspectSection` items evaluated at the `head` revision, and runs an LLM verification pass to produce structured `StalenessVerdict` diagnostics while flagging unlinked new public symbols separately.

## User Stories

1. As a repository maintainer, I want CoDoc to parse unified git diffs between `base` and `head` commits, so that only files touched in the pull request are analyzed.
2. As a repository maintainer, I want CoDoc to parse both `base` and `head` revisions of modified, added, and deleted Python files into `CodeChunk` sets, so that deleted or renamed symbols are represented accurately as `CodeDelta` pairs.
3. As a repository maintainer, I want diff hunk line ranges mapped onto AST chunk spans, so that modifying a line inside a function identifies the exact owning `CodeChunk` rather than just the file path.
4. As a repository maintainer, I want CoDoc to ignore test files, non-Python files, and user-configured ignore globs before AST analysis, so that test-only pull requests produce zero doc staleness noise.
5. As a repository maintainer, I want CoDoc to strip comments, docstrings, and whitespace formatting via AST normalization when comparing `old_chunk` and `new_chunk`, so that formatting or comment-only edits are classified as `skipped`.
6. As a repository maintainer, I want CoDoc to retain `CodeDelta` items with signature changes (parameters, type annotations, defaults, return types), decorator changes, class base changes, or schema/config field changes as `meaningful`, so that public API shifts are never dropped.
7. As a repository maintainer, I want CoDoc to also retain `CodeDelta` items whose executable AST body changed, so that behavioral changes (such as altered defaults, validation rules, or raised exceptions) are evaluated by the LLM verifier.
8. As a repository maintainer, I want CoDoc to query the `LinkGraph` for every `meaningful` `CodeDelta` using `old_chunk` and `new_chunk` identifiers, so that both modified and deleted symbols surface their linked `SuspectSection` candidates.
9. As a repository maintainer, I want CoDoc to deduplicate `SuspectSection` candidates when multiple `CodeDelta` items map to the same `DocSection`, bundling all relevant deltas into one verification context, so that the LLM reviews all related changes together and avoids duplicate warnings.
10. As a repository maintainer, I want each `SuspectSection` to be loaded from the PR's `head` revision (not `base`), so that if the author already updated the documentation in the same PR, the verifier marks it accurate instead of firing a false positive.
11. As a repository maintainer, I want CoDoc to enforce a configurable `max_suspects` budget cap (default `25`) ordered by link score, so that massive refactors never cause runaway LLM cost or CI timeouts.
12. As a documentation reviewer, I want each `SuspectSection` sent to a provider-agnostic LLM verifier with `old_chunk` source, `new_chunk` source, and `head` `DocSection` content, so that false positives from internal refactors are filtered out before repair.
13. As a documentation reviewer, I want each `StalenessVerdict` to return structured fields (`is_stale`, `diagnosis`, `change_complexity` as `simple` or `complex`, and `confidence` from `0.0` to `1.0`), so that Phase 3 can route fixes deterministically.
14. As a repository maintainer, I want CoDoc to detect newly added public `CodeChunk` symbols (endpoints, schemas, CLI commands, public functions/classes) that have zero `LinkEdge` entries in `LinkGraph`, so that Phase 4 can flag them as `needs new docs` without fabricating new doc pages.
15. As a developer testing the pipeline, I want a single change-detection entrypoint accepting a git diff/revision pair, a `LinkGraph`, and an injectable LLM client, so that the full Phase 2 flow can be verified deterministically without real network calls.

## Implementation Decisions

- **Step 2.1 — Dual-Revision Diff-to-Chunk Mapper**:
  - Parse unified diffs to collect changed file paths and per-file modified line ranges for `base` and `head`.
  - Run the Phase 1 Python AST parser on `base` and `head` contents of changed Python files (or treat added/deleted files as empty on the missing side) and align overlapping hunks to `CodeChunk` spans.
  - Emit `CodeDelta(delta_type: added | modified | deleted, old_chunk, new_chunk)` records.
- **Step 2.2 — Deterministic `ChangeFilterResult` Pipeline**:
  - Drop excluded paths (`tests/**`, migrations, non-`.py`, custom ignore globs).
  - Normalize `old_chunk` and `new_chunk` ASTs by removing comments, formatting trivia, and docstrings (`ast.unparse` on docstring-stripped ASTs) to detect no-op edits (`skipped`).
  - Classify changes affecting signatures, decorators, base classes, Pydantic/config fields, CLI options, or normalized executable AST bodies as `meaningful`.
  - Record unlinked newly added public `CodeChunk` items separately for reporting.
- **Step 2.3 — `SuspectSection` Resolution**:
  - Query `LinkGraph` (built or refreshed from `base` plus `head` doc parsing) for all edges touching `old_chunk.chunk_id` or `new_chunk.chunk_id`.
  - Resolve each suspect's text from the PR `head` Markdown tree; if a linked section was deleted in `head`, omit it from repair suspects unless another section still references the deleted code.
  - Group multiple `CodeDelta` records per `DocSection`, sort by highest `LinkEdge` score, and truncate cleanly at `max_suspects` (reporting truncation in metadata).
- **Step 2.4 — LLM `StalenessVerdict` Verifier**:
  - Define a thin provider-agnostic LLM client interface supporting structured JSON output (`verify_staleness(suspect) -> StalenessVerdict`), backing OpenAI (`gpt-4o`) and Anthropic/OpenAI-compatible models via `llm_api_key` and optional `base_url`.
  - Enforce strict structured schema (`is_stale: bool`, `diagnosis: str`, `change_complexity: "simple" | "complex"`, `confidence: float`) with bounded retry/error capture on malformed responses.
  - Expose one top-level Phase 2 entrypoint (`detect_stale_sections(diff_context, link_graph, llm_client, config)`).

## Testing Decisions

- **What makes a good test**: Feed realistic unified diffs and fixture repo revisions (`base` and `head`) plus a stubbed LLM client into the Phase 2 entrypoint, asserting `CodeDelta` pairing (including deletions), `ChangeFilterResult` skipping of comment/whitespace/test changes, `SuspectSection` resolution against `head` docs (verifying same-PR doc updates pass), `max_suspects` bounding, unlinked new-symbol detection, and structured `StalenessVerdict` outputs.
- **Modules tested**: Top-level change-detection entrypoint covering Steps 2.1–2.4 end to end, plus focused property/fixture tests for AST normalization edge cases (multiline docstrings, formatting changes, decorator argument changes, deleted functions).
- **Prior art**: Reuse Phase 1 `pytest` fixture repository conventions and fake provider interfaces.

## Out of Scope

- Generating or validating documentation rewrites (covered in Phase 3).
- Auto-generating brand-new documentation sections or files for unlinked new symbols (flagged only in summary).
- GitHub API calls, branch creation, or PR commenting (covered in Phase 4).

## Further Notes

- Canonical vocabulary follows `CONTEXT.md` (`CodeChunk`, `DocSection`, `LinkGraph`, `CodeDelta`, `ChangeFilterResult`, `SuspectSection`, `StalenessVerdict`).
- Evaluating `SuspectSection` at `head` eliminates the common false positive where a developer updates code and docs in the same PR.
