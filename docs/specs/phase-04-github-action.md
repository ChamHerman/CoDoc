# Phase 4: GitHub Action

**Steps covered:**
- **Step 4.1:** Package CoDoc as a containerized GitHub Action (`Dockerfile` + action manifest) with inputs, outputs, triggers, and CI guardrails
- **Step 4.2:** Implement safe PR workflow (`auto_fix` branch/PR creation, fork degradation, bot loop-guard, and `review_flag` comments)
- **Step 4.3:** Post structured `RunReport` summary comment on the triggering PR

---

## Problem Statement

A local CLI script cannot protect documentation automatically on every code change. Teams need a zero-infrastructure GitHub Action they can drop into any repository workflow that runs CoDoc safely on pull requests, opens clean fix PRs or review comments without risking fork secret leaks or infinite bot loops, and gives clear visibility into what was checked.

## Solution

Package Phases 1–3 into a Dockerized GitHub Action backed by a pre-built container image and explicit action manifest, integrating with GitHub via `PyGithub` to run cached incremental checks on pull requests (and optional post-merge pushes), open `auto_fix` PRs targeting the PR head branch on same-repo PRs (or degrading safely to read-only checks on forks), and post a single upserted `RunReport` summary comment linking all verified, fixed, flagged, and unlinked symbols.

## User Stories

1. As a repository maintainer, I want a ready-to-use GitHub Action manifest with documented inputs (`llm_api_key`, optional `embedding_api_key`, `llm_model`, `embedding_model`, `base_url`, `confidence_threshold`, `max_suspects`, `ignore_paths`, `auto_merge`, `fail_on_stale`), so that I can configure CoDoc in one workflow step.
2. As a repository maintainer, I want the Action to expose structured outputs (`stale_count`, `auto_fixed_count`, `flagged_count`, `unlinked_new_count`, `fix_pr_url`, `report_json`), so that downstream CI jobs can inspect CoDoc results.
3. As a repository maintainer, I want the Action backed by a reproducible container image (published to GHCR with a local `Dockerfile` fallback), so that workflow runs start quickly without compiling Python and ChromaDB dependencies from scratch on every PR.
4. As a repository maintainer, I want the Action to restore and save the `LinkGraph` and ChromaDB cache directory across runs, so that only changed files are re-embedded on repeat PRs.
5. As a repository maintainer, I want CoDoc to skip execution automatically when triggered by CoDoc's own branches (`codoc/*`) or bot actors, so that doc-fix PRs never trigger infinite self-referential Action runs.
6. As a repository maintainer, I want `auto_fix` commits on same-repo pull requests to go onto a dedicated `codoc/fix-pr-<number>` branch opening a PR targeting the **original PR's head branch** (and targeting `main` only when running post-merge on `push`), so that documentation for unmerged features never merges prematurely into `main`.
7. As a repository maintainer, I want each `auto_fix` PR body to list every updated `DocSection`, why it changed, and which source symbols triggered the update, so that maintainers can review and merge in seconds.
8. As a maintainer with `auto_merge` enabled (default `false`), I want the Action to only enable GitHub auto-merge on the generated doc-fix PR when repository permissions allow it and fail safely with a notice if restricted, so that protected-branch rules never crash the Action.
9. As a repository maintainer receiving pull requests from external forks, I want CoDoc to detect fork PR context and degrade gracefully (skipping branch push/PR creation if write tokens or secrets are unavailable, without failing the contributor's CI check), so that contributor workflows remain secure and green.
10. As a PR author, I want `review_flag` items posted on the triggering PR with exact file/line links, `heading_path`, Phase 2 `diagnosis`, and a collapsible suggested Markdown diff snippet, so that I can fix complex doc gaps immediately.
11. As a PR author, I want newly added unlinked public symbols surfaced in a separate `Needs New Documentation` list inside the PR comment, so that undocumented new features do not silently ship without docs.
12. As a PR author, I want CoDoc to upsert (create once, then update in place via marker tag) a single `RunReport` summary comment on the triggering PR (e.g., `Doc Check Results: 3 sections verified accurate, 1 auto-fixed (see PR #42), 2 flagged for review`), so that repeated pushes to a PR do not spam duplicate comments.
13. As a repository maintainer, I want `fail_on_stale` to default to `false` (advisory status) while supporting `true` for strict enforcement, so that teams can adopt CoDoc gradually before making it a blocking gate.
14. As a developer testing the Action, I want an Action runner entrypoint accepting parsed GitHub event context, env/inputs, and an injectable GitHub API client + pipeline runner, so that branch creation, PR opening, fork degradation, loop-guards, and comment upserting are tested without hitting live GitHub.

## Implementation Decisions

- **Step 4.1 — Container & Action Packaging**:
  - Define container image (`python:3.11-slim` base with pinned dependencies and non-root execution where compatible with GitHub workspace ownership) and Action manifest declaring all inputs, outputs, and `docker` runtime metadata.
  - Enforce guardrails in configuration defaults: `confidence_threshold=0.80`, `max_suspects=25`, `auto_merge=false`, `fail_on_stale=false`, and default ignore patterns (`tests/**`, `.github/**`, `migrations/**`).
  - Document recommended workflow trigger (`pull_request` / `push` on code/doc paths) and `permissions` (`contents: write`, `pull-requests: write`).
- **Step 4.2 — GitHub PR Workflow**:
  - Implement a thin GitHub integration adapter using `PyGithub` covering diff/ref discovery, branch creation/update, PR creation/lookup (idempotent reuse of existing open `codoc/fix-pr-<n>` PR), optional auto-merge request, and comment upsert by hidden HTML marker (`<!-- codoc-report -->`).
  - Loop-guard: exit cleanly with no-op status if PR head branch starts with `codoc/` or actor is `github-actions[bot]` on a CoDoc branch.
  - Fork handling: if `pull_request` originates from a fork (`head.repo.full_name != base.repo.full_name`) or lacks write/secret access, log clear skip/degrade status and exit `0` unless strictly configured otherwise.
  - Branch targeting invariant: for `pull_request` events in the same repo, `auto_fix` PR base is `pr.head.ref`; for `push` events on default branch, `auto_fix` PR base is the pushed branch.
- **Step 4.3 — `RunReport` Comment Builder**:
  - Render deterministic Markdown summary showing counts (`verified_accurate`, `auto_fixed`, `flagged_for_review`, `unlinked_new_symbols`, `truncated_suspects`), clickable blob/anchor links to each affected `DocSection`, link to the generated `auto_fix` PR if opened, and expandable `<details>` blocks with diagnoses and suggested Markdown snippets for `review_flag` items.
  - Emit Action outputs (`GITHUB_OUTPUT`) and exit non-zero only on unrecoverable internal errors or when `fail_on_stale=true` and stale items remain.

## Testing Decisions

- **What makes a good test**: Drive the Action runner entrypoint with synthetic GitHub event JSON payloads (`pull_request` same-repo, `pull_request` fork, `codoc/*` loop-guard, `push` to `main`) and a fake GitHub adapter + stubbed Phases 1–3 outputs, asserting exact PR base branch, idempotent PR reuse, marker-based comment upsert, fork safe degradation, and `GITHUB_OUTPUT` values.
- **Modules tested**: Action entrypoint / runner, GitHub workflow adapter, and `RunReport` comment formatter. Also validate container build and dry-run execution in CI.
- **Prior art**: Same `pytest` suite and fake-adapter boundary pattern from Phases 1–3.

## Out of Scope

- Publishing to the GitHub Actions Marketplace and recording portfolio demo videos (covered in Phase 6).
- Multi-repository cross-repo documentation syncing.
- GitLab CI or Bitbucket Pipelines adapters (interface remains clean, but v1 implements GitHub Actions only).

## Further Notes

- Canonical vocabulary follows `CONTEXT.md` (`DocSection`, `CodeChunk`, `StalenessVerdict`, `RepairPatch`, `RepairMode`, `RunReport`).
- Targeting the PR head branch instead of `main` prevents out-of-order doc merges and keeps feature code and feature docs synchronized inside the same PR lifecycle.
