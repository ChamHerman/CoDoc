# 🎬 CoDoc Demonstration Package: Screenplay, Storyboard & Production Guide

This document specifies the complete, scene-by-scene demonstration package for **CoDoc** (`v1.0.0`), showcasing autonomous code-to-documentation synchronization and self-healing pull requests in under 3 minutes.

---

## 📋 Video Production Specifications

| Specification | Target Parameter |
| :--- | :--- |
| **Target Runtime** | **2 minutes 45 seconds** (Strictly < 3:00 minutes) |
| **Video Resolution** | 1920 × 1080 (1080p Full HD, 60 FPS) |
| **Aspect Ratio** | 16:9 Widescreen |
| **Color Space** | Rec.709, sRGB |
| **Audio Mix** | Vocal voiceover centered at -14 LUFS; Lo-fi ambient tech beat ducked at -24 dB |
| **Voiceover Persona** | Confident, articulate developer advocate / platform engineer; conversational yet authoritative |
| **Visual Style** | Minimalist modern developer aesthetic; Dark theme (GitHub Dark / Catppuccin Macchiato); smooth cursor panning |
| **Key Assets** | [docs/assets/demo-preview.svg](assets/demo-preview.svg), [docs/assets/demo-hero-poster.svg](assets/demo-hero-poster.svg) |

---

## ⏱️ High-Level Scene Timeline

```text
0:00 ─── [Act 1: The Problem Hook] ──────── 0:30
0:30 ─── [Act 2: Feature Branch & PR Push] ── 1:05
1:05 ─── [Act 3: Autonomous CI Action Run] ── 1:45
1:45 ─── [Act 4: Diagnostic RunReport] ────── 2:15
2:15 ─── [Act 5: Auto-Fix Companion PR] ───── 2:45
```

---

## 🎭 Scene-by-Scene Screenplay & Storyboard

### Act 1: The Documentation Drift Dilemma (`0:00 - 0:30` | 30s)

* **Goal**: Hook the audience with the ubiquitous pain of silent documentation rot where unit tests pass, but production documentation breaks.
* **Visual Setup**:
  * Split-screen view: Left pane displays `src/payments/risk.py` with passing green unit tests.
  * Right pane displays `docs/architecture/risk.md` showing a stale code snippet that lacks new parameters.
  * Camera slowly zooms in on the missing parameter discrepancy.
* **On-Screen Graphics**:
  * Title Card: `CoDoc — The Self-Healing Documentation Guard`
  * Callout Box: `❌ Documentation Drift: Code changed, docs stayed still`
* **Audio / Voiceover**:
  > *"Every engineering team knows this pain: your unit tests pass, your feature ships, but your documentation secretly rots. A developer refactors a function signature or adds a required keyword argument, but forgets to update the architecture docs. Weeks later, onboarding developers and API consumers hit confusing runtime errors because the docs describe code that no longer exists.*
  >
  > *What if your CI pipeline could detect, verify, and heal documentation drift autonomously on every single pull request? Meet CoDoc."*
* **Sound Effects (SFX)**:
  * Subtle digital transition whoosh at `0:00`.
  * Passing test chime at `0:10`.
  * Subtle warning buzz when highlighting stale doc at `0:22`.

---

### Act 2: Feature Branch & API Signature Change (`0:30 - 1:05` | 35s)

* **Goal**: Show a realistic developer workflow creating a pull request that introduces documentation rot.
* **Visual Setup**:
  * IDE window (VS Code / JetBrains) showing Python code.
  * Developer updates `calculate_risk_score` in `src/payments/risk.py`:
    ```python
    # Before:
    def calculate_risk_score(tx_id: str) -> float: ...

    # After:
    def calculate_risk_score(tx_id: str, *, weights: dict[str, float] | None = None) -> float: ...
    ```
  * Developer executes git commands in the integrated terminal:
    ```bash
    git checkout -b feature/risk-weight-matrix
    git commit -am "feat(risk): support dynamic risk weight overrides"
    git push origin feature/risk-weight-matrix
    ```
  * Transition to GitHub browser window: Developer clicks **Open Pull Request #42**.
* **On-Screen Graphics**:
  * Floating badge: `Pull Request #42 Opened`
  * Text banner: `Unified Diff: +2 -1 lines in payments/risk.py (docs/ untouched)`
* **Audio / Voiceover**:
  > *"Here, our developer introduces a new keyword-only weights dictionary to our payment risk engine. The feature is clean, type-checked, and ready to merge.*
  >
  > *However, our architecture guide in `docs/architecture/risk.md` still documents the single-argument version from six months ago. The developer pushes their branch and opens Pull Request 42."*
* **SFX**:
  * Clean mechanical keyboard typing sounds (`0:35 - 0:48`).
  * Terminal command submission click.

---

### Act 3: Autonomous GitHub Actions CI Execution (`1:05 - 1:45` | 40s)

* **Goal**: Illustrate how CoDoc executes inside standard GitHub Actions CI, parsing diffs, traversing the LinkGraph, and verifying staleness without noise.
* **Visual Setup**:
  * GitHub Actions tab in browser. Workflow `codoc-ci-gate.yml` begins executing.
  * Live log terminal unfolds showing CoDoc's 4-stage pipeline:
    1. **AST Diff Normalization**: Shows `ast.unparse` filtering out comments and whitespace trivia.
    2. **LinkGraph Resolution**: FastEmbed CPU queries local ChromaDB collection; isolates `docs/architecture/risk.md#risk-scoring-api` in 85ms.
    3. **LLM Staleness Verification**: Structured JSON prompt returns `is_stale: true`, `confidence: 0.94`, `repair_class: "simple"`.
    4. **3-Criteria Validator Gate**: Independent validator scores factual accuracy (1.00), prose preservation (0.98), and style consistency (1.00).
  * Elapsed execution timer prominently ticks from `0.0s` to `24.3s`.
* **On-Screen Graphics**:
  * Animated pipeline flowchart highlighting each phase:
    `AST Diff Filter ➔ LinkGraph ChromaDB ➔ LLM Judge ➔ 3-Criteria Validator Gate`
  * Metric badge: `⚡ Total CI Run Time: 24.3s (FastEmbed CPU + GPT-4o-mini)`
* **Audio / Voiceover**:
  > *"Instantly, CoDoc kicks off directly inside GitHub Actions. First, its AST-aware normalizer inspects the unified diff, ignoring cosmetic whitespace and comment changes to avoid wasted cycles.*
  >
  > *Second, CoDoc traverses its pre-indexed LinkGraph—powered by FastEmbed on local runner CPU and ChromaDB—pinpointing exactly which Markdown documentation sections cite this modified symbol.*
  >
  > *Third, a structured LLM judge evaluates semantic staleness with strict JSON outputs. Finally, candidate repairs must pass a rigorous 3-criteria quality gate checking factual accuracy, prose preservation, and style consistency. All in under twenty-five seconds."*
* **SFX**:
  * Ambient low electronic hum.
  * 4 quick successive verification check sounds as each pipeline phase succeeds.

---

### Act 4: Diagnostic RunReport PR Comment (`1:45 - 2:15` | 30s)

* **Goal**: Showcase the developer experience on the PR page with clear, actionable diagnostics and zero alert fatigue.
* **Visual Setup**:
  * Browser switches back to GitHub PR #42.
  * A single, beautifully formatted markdown comment authored by `github-actions[bot]` appears.
  * Cursor scrolls through the RunReport comment:
    * Executive status badge: `Status: Action Required • 1 Stale Section Detected`.
    * Breakdown table showing `docs/architecture/risk.md` with line numbers.
    * Suggested diff block highlighting the exact doc patch.
    * Loop-guard confirmation pill showing safe bot execution.
* **On-Screen Graphics**:
  * Highlight box around the single-comment marker (`<!-- codoc-report -->`).
  * Callout: `✨ In-Place Upserting: No spam, 1 persistent comment per PR`.
* **Audio / Voiceover**:
  > *"Back on Pull Request 42, CoDoc has posted a crystal-clear diagnostic RunReport. Notice that CoDoc never spams your pull request: it uses invisible metadata markers to create and update a single in-place comment throughout the PR lifecycle.*
  >
  > *The report highlights the exact stale heading, explains why the code signature drifted, and provides a formatted preview of the required documentation repair."*
* **SFX**:
  * Soft UI pop sound when comment renders.

---

### Act 5: One-Click Auto-Fix Companion PR & Outro (`2:15 - 2:45` | 30s)

* **Goal**: Show the self-healing resolution where CoDoc opens a companion pull request, restoring documentation sync to 100%.
* **Visual Setup**:
  * Screen transitions to GitHub pull request list.
  * A new companion PR #43 is created: `[CoDoc] Synchronize risk.md with calculate_risk_score` on branch `codoc/fix-pr-42`.
  * Click into PR #43: Unified diff reveals the exact markdown update—only the code block and parameter description are patched; all surrounding prose, headers, and callouts remain intact.
  * Maintainer clicks **Merge Pull Request**.
  * PR #42 CI checks re-run: CoDoc reports `Verified Accurate: 1 | Stale: 0 | Auto-Fixed: 0`. Status badge flips to `100% In Sync (Green)`.
  * Outro screen with GitHub Marketplace badge and CLI install command.
* **On-Screen Graphics**:
  * Banner: `🚀 1-Click Self-Healing: Companion PR Merged`
  * End Screen:
    * `GitHub Actions Marketplace: ChamHerman/CoDoc@v1`
    * `pip install codoc`
    * `https://github.com/ChamHerman/CoDoc`
* **Audio / Voiceover**:
  > *"Even better: because the repair passed the quality gate with ninety-four percent confidence, CoDoc automatically opened companion Pull Request 43 on an isolated branch.*
  >
  > *With one click, the maintainer merges the documentation fix. Pull Request 42 turns completely green, and documentation rot is eliminated before your code ever reaches production.*
  >
  > *Stop chasing stale documentation. Add CoDoc to your GitHub Actions workflow today or install the CLI with `pip install codoc`."*
* **SFX**:
  * Satisfying merge click sound (`2:28`).
  * Cheerful success chime as PR check turns green (`2:35`).
  * Upbeat outro chord fading out (`2:40 - 2:45`).

---

## 🛠️ Screen Recording & Production Checklist

Before recording the live video walkthrough, ensure all environment parameters match the standard:

### 1. Environment & Display Configuration
- [ ] Set monitor resolution to 1920 × 1080 (100% DPI scaling).
- [ ] Set browser zoom level to 125% for readable GitHub UI typography.
- [ ] Enable GitHub Dark theme (`Settings ➔ Appearance ➔ Dark default`).
- [ ] VS Code theme set to standard GitHub Dark or Catppuccin Macchiato.
- [ ] Font: `Fira Code` or `JetBrains Mono` at 16px with ligatures enabled.
- [ ] Silence all operating system notifications, dock badges, and background audio.

### 2. Git & Repository Sandbox State
- [ ] Verify test repository has `codoc` GitHub Action workflow configured (`.github/workflows/codoc.yml`).
- [ ] Pre-index codebase with `codoc index . --output .codoc/link_graph.json` so ChromaDB collection is primed.
- [ ] Prepare clean demo branch `feature/risk-weight-matrix` with a single unstaged modification in `src/payments/risk.py`.
- [ ] Ensure `docs/architecture/risk.md` contains the known baseline code snippet.

### 3. Audio & Vocal Recording
- [ ] Professional cardioid / dynamic USB microphone positioned 15cm from speaker.
- [ ] Apply noise gate, subtle 80Hz high-pass filter, and soft vocal compression (2:1 ratio).
- [ ] Record narration in a single continuous pass per act for consistent vocal tone and energy.

---

## 🔗 Related Documentation & References

- [README.md](../README.md): Main repository documentation, architecture overview, and quickstart guide.
- [docs/RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md): Step-by-step pre-release publication and Marketplace release gate.
- [benchmarks/results.md](../benchmarks/results.md): Real-world evaluation results across FastAPI and Pydantic test suites.
- [action.yml](../action.yml): GitHub Actions manifest with Marketplace branding and configuration schema.
