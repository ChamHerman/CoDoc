# Phase 6: Portfolio Polish & Marketplace Release

**Steps covered:**
- **Step 6.1:** Publish CoDoc to the GitHub Actions Marketplace with release tags, branding, and install documentation
- **Step 6.2:** Record a concise (<3 min) end-to-end demo and polish README walkthrough

---

## Problem Statement

Even a working codebase fails to deliver adoption or portfolio impact if other developers cannot install the GitHub Action in minutes or quickly see how it behaves on a real pull request without reading hundreds of lines of setup instructions.

## Solution

Finalize CoDoc for public adoption by adding GitHub Actions Marketplace metadata, semantic version release tags (e.g., `v1` / `v1.0.0`), copy-paste installation workflow examples, and a clean README showcasing the architecture, Phase 5 accuracy benchmarks, and a sub-3-minute demo video walking from code push to PR comment and auto-generated doc-fix PR.

## User Stories

1. As a developer evaluating CoDoc, I want complete GitHub Actions Marketplace branding metadata (icon, color, description, inputs/outputs docs) in the action manifest, so that CoDoc appears cleanly on the Marketplace and installs via a single `uses:` line.
2. As a developer installing CoDoc, I want immutable version tags and a major version alias (`v1`), so that my CI workflow remains stable across patch updates.
3. As a developer installing CoDoc, I want a copy-paste minimal workflow YAML snippet in the README covering required secrets and permissions, so that I can adopt CoDoc in under two minutes.
4. As a developer evaluating CoDoc, I want an architecture overview diagram and clear explanation of Phases 1–4 inside the README, so that I understand how Code-to-Docs mapping, AST filtering, validation gates, and PR workflows fit together.
5. As an interviewer or evaluator, I want a <3-minute demo video embedded/linked at the top of the README showing: (1) the problem statement, (2) a real code change pushed in a PR, (3) the Action running, (4) the `RunReport` PR comment appearing, and (5) the auto-generated doc-fix PR, so that end-to-end value is obvious immediately.
6. As a maintainer releasing CoDoc, I want a clean container image release workflow (pushing tagged image to GHCR alongside git release tags), so that Marketplace users pull a fast pre-built image rather than building from source on every run.

## Implementation Decisions

- **Step 6.1 — Marketplace Readiness & Release Pipeline**:
  - Add Marketplace `branding` (`icon`, `color`) and complete input/output descriptions to the Action manifest.
  - Add release automation publishing tagged Docker images to GHCR and validating that the Marketplace `uses: ChamHerman/CoDoc@v1` installation path passes a clean smoke-test workflow on a sample repository.
  - Ensure repository root includes clear `README` sections: Problem/Solution, Quickstart Workflow, Configuration Reference, Architecture, Evaluation Benchmarks (from Phase 5), and Demo Link.
- **Step 6.2 — Demo Recording & Walkthrough**:
  - Record a crisp <3-minute screen capture on a real repository PR demonstrating: problem hook ("every team's docs are perpetually stale"), code change PR pushed, Action executing, `RunReport` summary comment posted, and `auto_fix` PR opened with clean Markdown diff.
  - Host/link the demo asset cleanly from the repository README without bloating git history with large raw video files.

## Testing Decisions

- **What makes a good test**: Automated validation checks on the Action manifest schema/metadata and a CI release smoke check verifying the tagged container image executes cleanly against a minimal fixture repo (`workflow_dispatch` / dry-run check). Demo recording and Marketplace UI appearance are verified manually via checklist before release.
- **Modules tested**: Action manifest/packaging metadata and container release smoke test.
- **Prior art**: Standard GitHub Actions metadata validation and containerized smoke workflow.

## Out of Scope

- Paid SaaS hosting, user accounts, or multi-tenant billing dashboard.
- Custom IDE extensions (VS Code / JetBrains plugins).
- Multi-language parsers beyond the v1 Python scope.

## Further Notes

- Phase 6 depends on Phases 4 and 5 being green so that the Marketplace release points to a verified image and the README reports genuine Phase 5 evaluation numbers.
