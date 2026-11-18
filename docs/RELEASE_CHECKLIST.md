# 🚀 CoDoc Pre-Release & Marketplace Publication Checklist

This checklist defines the rigorous release verification protocol required before publishing **CoDoc** (`v1.0.0`) to the **GitHub Actions Marketplace** and **GitHub Container Registry (GHCR)**.

---

## 📦 Phase 1: Action Manifest & Marketplace Metadata

- [ ] **Marketplace Branding Attributes in `action.yml`**:
  - `name`: Must be descriptive, title-cased, and unique on Marketplace (`CoDoc — Self-Healing Documentation Guard`).
  - `description`: Must be concise and under 125 characters (`Continuous documentation synchronization, staleness detection, and self-healing repair action for Python repositories.`).
  - `branding.icon`: Verified Feather icon name supported by GitHub Actions (`book-open`).
  - `branding.color`: Supported color string (`blue`).
- [ ] **Input & Output Schema Completeness**:
  - All 11 configuration parameters documented with default values, types, and descriptions (`github_token`, `llm_api_key`, `llm_model`, `confidence_threshold`, `max_suspects`, `ignore_paths`, `auto_merge`, `fail_on_stale`, `base_url`, `embedding_api_key`, `embedding_model`).
  - Output variables documented (`verified_accurate`, `auto_fixed`, `flagged_for_review`, `stale_detected`, `fix_pr_number`, `fix_branch`).
- [ ] **Execution Mode**:
  - `runs.using`: Configured as `docker`.
  - `runs.image`: References `docker://ghcr.io/chamherman/codoc:v1.0.0` (or `Dockerfile` during local checkout testing).

---

## 🐳 Phase 2: Docker Container Build & GHCR Publication

- [ ] **Multi-Stage Dockerfile Verification**:
  - Base image uses modern Python slim runtime (`python:3.11-slim`).
  - Multi-stage build separates compiler build dependencies from final runtime container.
  - Final image size strictly under 180 MB.
  - Non-root user configuration (`appuser:appgroup`) to adhere to container security standards.
- [ ] **GHCR Publishing Automation (`.github/workflows/release.yml`)**:
  - Workflow triggers on semantic release tags (`v*.*.*`).
  - GitHub Actions permissions set to `packages: write` and `contents: write`.
  - Buildx cache enabled for accelerated layer reuse.
  - Dual tag publication: pinned version (`ghcr.io/chamherman/codoc:v1.0.0`) and floating major tag (`ghcr.io/chamherman/codoc:v1`).

---

## 🏷️ Phase 3: Versioning & Git Tagging Protocol

- [ ] **Semantic Versioning Compliance**:
  - Release follows SemVer `vX.Y.Z` (`v1.0.0`).
  - Git commit history clean with conventional commits (`feat(...)`, `fix(...)`, `docs(...)`).
- [ ] **Major Floating Tag Management**:
  - After tagging `v1.0.0`, force-update the floating major tag `v1`:
    ```bash
    git tag -fa v1 -m "Release CoDoc v1 (points to v1.0.0)"
    git push origin v1 --force
    ```
- [ ] **GitHub Release Drafting**:
  - Release title: `CoDoc v1.0.0 — Continuous Self-Healing Documentation Engine`.
  - Release body includes changelog, benchmark summary (100% accuracy on FastAPI & Pydantic), and Marketplace quickstart snippet.

---

## 🧪 Phase 4: Test Suite, Typing & Benchmark Verification Gate

- [ ] **Pytest Test Suite (100% Passing)**:
  - Run full test suite:
    ```bash
    uv run pytest
    ```
  - Verify zero test failures and zero unhandled warnings across all modules (AST parser, LinkGraph ChromaDB indexer, diff normalizer, LLM judge, 3-criteria gate, action runner).
- [ ] **Strict MyPy Static Analysis**:
  - Run strict typing verification:
    ```bash
    uv run mypy src tests
    ```
  - Verify `Success: no issues found in 38+ source files`.
- [ ] **Real-World Benchmark Verification**:
  - Run benchmark harness against manifest:
    ```bash
    codoc evaluate --manifest benchmarks/manifest.json --output-json benchmarks/results.json
    ```
  - Verify metrics meet or exceed release thresholds:
    - Precision $\ge 0.90$ (Achieved: $1.00$)
    - Recall $\ge 0.90$ (Achieved: $1.00$)
    - F1 Score $\ge 0.90$ (Achieved: $1.00$)
    - False Positive Rate $\le 0.10$ (Achieved: $0.00$)

---

## 🎬 Phase 5: Demo Package & Asset Verification

- [ ] **Demo Video Storyboard Review (`docs/DEMO_STORYBOARD.md`)**:
  - Verify complete 5-act screenplay: Problem Hook, PR Code Push, CI Action Run, RunReport Comment, Auto-Fix PR.
  - Total video duration strictly under 3 minutes (Target: 2m 45s).
  - Production recording guidelines verified (1080p, 60fps, GitHub Dark theme, clean audio).
- [ ] **Vector SVG Assets Integrity**:
  - `docs/assets/demo-preview.svg` exists, contains valid SVG markup, and renders crisp in both dark and light modes.
  - `docs/assets/demo-hero-poster.svg` exists and adheres to 16:9 aspect ratio (1280x720).
- [ ] **Hero README Showcase Integration**:
  - Prominent clickable demo showcase banner present near top of `README.md`.
  - Markdown links to `docs/DEMO_STORYBOARD.md` and related documentation resolve without 404s.

---

## 🔒 Phase 6: Security, Fork Isolation & Secret Sanitization

- [ ] **Fork PR Isolation**:
  - Verified that `is_fork` pull requests gracefully disable branch creation and auto-fix pushes to protect repository secrets.
- [ ] **Loop-Guard Circuit Breaker**:
  - Verified that runs initiated by `github-actions[bot]` or on `codoc/*` branches exit immediately with code 0 to prevent infinite CI loops.
- [ ] **Zero Hardcoded Secrets**:
  - Audited codebase and documentation examples to guarantee zero real API keys or tokens are checked into git.

---

## 🌐 Phase 7: Post-Publication Smoke Test

- [ ] **Marketplace Listing Sanity**:
  - Confirm page renders correctly on `https://github.com/marketplace/actions/codoc-self-healing-documentation-guard`.
  - Confirm "Use latest version" displays `v1` and tags correctly.
- [ ] **Live Smoke Test on External Template Repo**:
  - Create a clean test PR in a separate consumer repository using:
    ```yaml
    uses: ChamHerman/CoDoc@v1
    with:
      github_token: ${{ secrets.GITHUB_TOKEN }}
      llm_api_key: ${{ secrets.OPENAI_API_KEY }}
    ```
  - Verify the action pulls the GHCR image, performs AST diffing, posts the in-place RunReport comment, and completes under 30 seconds.
