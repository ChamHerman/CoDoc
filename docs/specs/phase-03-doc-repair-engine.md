# Phase 3: Doc Repair Engine

**Steps covered:**
- **Step 3.1:** Generate targeted Markdown corrections (`RepairPatch`) for confirmed stale sections
- **Step 3.2:** Validate generated corrections (`ValidationVerdict`) through a second LLM quality gate
- **Step 3.3:** Route repairs by confidence, complexity, and validation outcome (`RepairMode`: `auto_fix` vs `review_flag`)

---

## Problem Statement

Detecting stale documentation still leaves engineers with manual rewriting work, yet blindly letting an LLM rewrite entire documentation files introduces factual hallucinations, erases accurate explanations, changes tone, or commits messy `TODO` placeholders into production repositories.

## Solution

Build a Doc Repair Engine that takes each confirmed stale `StalenessVerdict` from Phase 2, generates a minimal `RepairPatch` preserving accurate content and surrounding Markdown structure, validates the patch in an independent second LLM pass (`ValidationVerdict`), and routes outcomes cleanly: simple, high-confidence, validated fixes become `auto_fix` patches ready for PR application, while complex, low-confidence, or validation-failed items become `review_flag` findings containing a diagnosis and suggested draft diff snippet for PR comments—never committing `TODO` markers into repository files.

## User Stories

1. As a documentation maintainer, I want CoDoc to rewrite only the stale parts of a `DocSection` using the current `head` section text, `new_chunk` source, and Phase 2 `diagnosis`, so that accurate paragraphs, examples, and tone remain untouched.
2. As a documentation maintainer, I want generated `RepairPatch` edits to preserve Markdown formatting, heading levels, admonitions, and list structure, so that automated updates match repository conventions.
3. As a documentation maintainer, I want CoDoc to run an independent second LLM pass (`ValidationVerdict`) on every generated `RepairPatch`, so that flawed or over-eager rewrites are caught before opening a pull request.
4. As a documentation maintainer, I want the validator pass to check three explicit criteria: factual accuracy against `new_chunk`, preservation of still-accurate content, and style consistency with the surrounding document, so that quality gates are auditable.
5. As a repository maintainer, I want the validator pass to return structured reasons when rejecting a patch, so that reviewers can see why auto-repair was withheld.
6. As a repository maintainer, I want simple, deterministic-style updates (such as renamed parameters, updated default values, changed type hints, or updated CLI flags) with `confidence >= threshold` and a passing `ValidationVerdict` to route to `auto_fix`, so that trivial doc updates require minimal effort.
7. As a repository maintainer, I want complex changes (such as new behavioral semantics, removed capabilities, or multi-concept architectural shifts) to route to `review_flag` even when the LLM proposes text, so that nuanced documentation stays human-authored.
8. As a repository maintainer, I want any patch whose `ValidationVerdict` fails or whose `confidence` is below the configured threshold to fall back to `review_flag`, so that uncertain edits never land in an auto-fix branch.
9. As a repository maintainer, I want `review_flag` items to never insert `TODO` markers into committed Markdown files, so that merged branches never retain placeholder litter.
10. As a reviewer reading a `review_flag` finding, I want CoDoc to include the Phase 2 `diagnosis` plus a suggested Markdown diff/draft snippet inside the review output, so that I can accept or adapt the fix quickly in the PR review.
11. As a repository maintainer, I want multiple `RepairPatch` items touching different sections of the same Markdown file to apply cleanly without line-offset collisions, so that multi-section repairs produce valid combined Markdown.
12. As a developer testing the engine, I want a single repair-engine entrypoint accepting stale diagnostics, `head` `DocSection` texts, `new_chunk` sources, and an injectable LLM client, so that generation, validation, and routing can be tested end to end with deterministic stubs.

## Implementation Decisions

- **Step 3.1 — Targeted `RepairPatch` Generator**:
  - Invoke the provider-agnostic LLM client with `head` `DocSection` content, optional neighboring section style context, `new_chunk` source, and `StalenessVerdict.diagnosis`.
  - Produce `RepairPatch(section_id, doc_path, original_content, proposed_content, unified_diff, summary)` constrained to the target section span; skip generation if the LLM proposes an empty or identical change.
- **Step 3.2 — Independent `ValidationVerdict` Gate**:
  - Run a separate LLM evaluation step receiving `(original_content, proposed_content, new_chunk, diagnosis)`—without sharing the generator's reasoning chain—returning `ValidationVerdict(passed: bool, accuracy_ok: bool, preservation_ok: bool, style_ok: bool, issues: list[str])`.
  - Require all three boolean checks (`accuracy_ok`, `preservation_ok`, `style_ok`) to be true for `passed == True`.
- **Step 3.3 — `RepairMode` Router & Patch Composer**:
  - Route to `auto_fix` if and only if: `verdict.change_complexity == "simple"` AND `verdict.confidence >= confidence_threshold` (default `0.80`) AND `validation.passed is True`.
  - Route all other confirmed stale items (complex changes, below-threshold confidence, or failed validation) to `review_flag` carrying `diagnosis`, `validation.issues`, and the proposed snippet for PR comment display (never written to repo files).
  - Apply `auto_fix` patches cleanly by section line span in reverse line order per document so multiple repairs in one file never shift prior offsets.
  - Expose one top-level Phase 3 entrypoint (`repair_stale_sections(stale_items, config, llm_client) -> RepairPlan`).

## Testing Decisions

- **What makes a good test**: Drive the top-level Phase 3 entrypoint with stubbed LLM responses across all routing branches: (1) simple + high confidence + validator pass -> `auto_fix` with clean multi-section file rewrite, (2) simple + high confidence + validator fail -> downgraded to `review_flag` with no file mutation, (3) complex change -> `review_flag` with draft snippet and zero `TODO` markers written to files, and (4) low confidence -> `review_flag`.
- **Modules tested**: Top-level Repair Engine entrypoint covering Steps 3.1–3.3, plus reverse-order multi-section patch composition verification on multi-heading Markdown fixtures.
- **Prior art**: Same `pytest` and injectable stub LLM client convention established in Phases 1–2.

## Out of Scope

- Creating git branches, committing files, or posting GitHub PRs/comments (covered in Phase 4).
- Generating brand-new Markdown documents or adding missing sections for undocumented features.
- Auto-merging pull requests without user opt-in.

## Further Notes

- Canonical vocabulary follows `CONTEXT.md` (`DocSection`, `CodeChunk`, `StalenessVerdict`, `RepairPatch`, `ValidationVerdict`, `RepairMode`).
- This design resolves the contradiction in the original PDF between `TODO` draft files and PR review comments by keeping suggested drafts cleanly inside PR comments while reserving git commits strictly for validated high-confidence fixes.
