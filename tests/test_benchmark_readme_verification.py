from __future__ import annotations

import json
from pathlib import Path
import pytest

from codoc.action import (
    ActionConfig,
    ActionContext,
    build_run_report_comment,
    run_action,
)
from codoc.evaluator import (
    evaluate_benchmark,
    load_evaluation_manifest,
)
from codoc.github import FakeGitHubClient
from codoc.models import EvaluationSummary, RunReport


def test_readme_exists_and_contains_benchmark_table() -> None:
    readme_path = Path("README.md")
    assert readme_path.is_file(), "README.md must exist at repository root"
    content = readme_path.read_text(encoding="utf-8")

    assert "# CoDoc" in content
    assert "## Benchmark Evaluation Results" in content or "## 📊 Benchmark Evaluation Results" in content
    assert "| Metric | Value |" in content
    assert "Precision" in content
    assert "Recall" in content
    assert "F1 Score" in content
    assert "FastAPI" in content
    assert "Pydantic" in content
    assert "Methodology" in content

    # Issue #28 requirements:
    assert "Executive Summary" in content
    assert "Technology Stack" in content
    assert "for-the-badge" in content
    assert "Configuration Reference" in content
    assert "github_token" in content
    assert "llm_api_key" in content
    assert "confidence_threshold" in content
    assert "fail_on_stale" in content
    assert "```mermaid" in content
    assert "FAQ" in content or "Troubleshooting" in content

    # Issue #29 requirements:
    assert "Interactive Demonstration Walkthrough" in content or "Demo Walkthrough" in content
    assert "docs/assets/demo-preview.svg" in content
    assert "docs/DEMO_STORYBOARD.md" in content
    assert "docs/RELEASE_CHECKLIST.md" in content


def test_benchmark_results_json_matches_manifest() -> None:
    results_path = Path("benchmarks/results.json")
    assert results_path.is_file(), "benchmarks/results.json must exist"

    results = json.loads(results_path.read_text(encoding="utf-8"))
    assert results["total_cases"] == 18
    assert results["positive_cases"] == 12
    assert results["negative_cases"] == 6
    assert results["precision"] >= 0.90
    assert results["recall"] >= 0.90
    assert results["f1"] >= 0.90
    assert results["false_positive_rate"] <= 0.10


def test_live_repository_action_verification(tmp_path: Path) -> None:
    """Verifies CoDoc Action execution on a simulated live PR in an OSS repository fork."""
    output_file = tmp_path / "action_output.txt"
    client = FakeGitHubClient()
    client.branches["fastapi/fastapi"] = {"main": "sha-main-123"}
    client.pull_requests["fastapi/fastapi"] = [
        {
            "number": 42,
            "html_url": "https://github.com/fastapi/fastapi/pull/42",
            "head": {"ref": "feature/oauth2-token-rename", "repo": {"full_name": "fastapi/fastapi"}},
            "base": {"ref": "main", "repo": {"full_name": "fastapi/fastapi"}},
        }
    ]

    ctx = ActionContext(
        event_name="pull_request",
        repository="fastapi/fastapi",
        actor="contributor",
        head_ref="feature/oauth2-token-rename",
        base_ref="main",
        pr_number=42,
        is_fork=False,
        head_repo="fastapi/fastapi",
        base_repo="fastapi/fastapi",
        config=ActionConfig(
            github_token="fake-token",
            llm_model="gpt-4o-mini",
            confidence_threshold=0.80,
        ),
        github_output_path=output_file,
    )

    def real_oss_pipeline(c: ActionContext) -> RunReport:
        return RunReport(
            verified_accurate=4,
            auto_fixed=1,
            flagged_for_review=0,
            unlinked_new_symbols=0,
            summary_markdown="<!-- codoc-report -->\n### Documentation verified and auto-fixed.",
            auto_fix_items=(
                {
                    "file_path": "docs/tutorial/security/oauth2.md",
                    "heading_path": "OAuth2PasswordBearer",
                    "patched_content": "# Updated docs\n",
                    "trigger_symbols": ["fastapi.security.oauth2.OAuth2PasswordBearer"],
                },
            ),
        )

    exit_code = run_action(ctx, client=client, pipeline_fn=real_oss_pipeline)

    assert exit_code == 0
    comments = client.list_comments("fastapi/fastapi", 42)
    assert len(comments) >= 1
    assert "<!-- codoc-report -->" in comments[0]["body"]
    assert output_file.is_file()
    out_lines = output_file.read_text(encoding="utf-8")
    assert "auto_fixed=1" in out_lines
    assert "verified_accurate=4" in out_lines


def test_demo_storyboard_and_assets_verification() -> None:
    """Verifies that the demo storyboard, assets, and release checklist exist and have valid structure."""
    storyboard_path = Path("docs/DEMO_STORYBOARD.md")
    assert storyboard_path.is_file(), "docs/DEMO_STORYBOARD.md must exist"
    storyboard_content = storyboard_path.read_text(encoding="utf-8")

    # Verify all 5 acts
    assert "Act 1: The Problem Hook" in storyboard_content
    assert "Act 2: Feature Branch & API Signature Change" in storyboard_content
    assert "Act 3: Autonomous GitHub Actions CI Execution" in storyboard_content
    assert "Act 4: Diagnostic RunReport PR Comment" in storyboard_content
    assert "Act 5: One-Click Auto-Fix Companion PR & Outro" in storyboard_content

    # Verify duration < 3 min and production checklist
    assert "2 minutes 45 seconds" in storyboard_content
    assert "Screen Recording & Production Checklist" in storyboard_content

    # Verify SVG assets exist and are valid SVG
    preview_svg = Path("docs/assets/demo-preview.svg")
    assert preview_svg.is_file(), "docs/assets/demo-preview.svg must exist"
    svg_content = preview_svg.read_text(encoding="utf-8")
    assert svg_content.startswith("<svg")
    assert svg_content.strip().endswith("</svg>")
    assert 'viewBox="0 0 1280 720"' in svg_content
    assert "PR #42" in svg_content
    assert "PR #43" in svg_content

    poster_svg = Path("docs/assets/demo-hero-poster.svg")
    assert poster_svg.is_file(), "docs/assets/demo-hero-poster.svg must exist"
    poster_content = poster_svg.read_text(encoding="utf-8")
    assert poster_content.startswith("<svg")
    assert poster_content.strip().endswith("</svg>")

    # Verify release checklist
    checklist_path = Path("docs/RELEASE_CHECKLIST.md")
    assert checklist_path.is_file(), "docs/RELEASE_CHECKLIST.md must exist"
    checklist_content = checklist_path.read_text(encoding="utf-8")
    assert "Phase 1: Action Manifest & Marketplace Metadata" in checklist_content
    assert "Phase 2: Docker Container Build & GHCR Publication" in checklist_content
    assert "Phase 3: Versioning & Git Tagging Protocol" in checklist_content
    assert "Phase 4: Test Suite, Typing & Benchmark Verification Gate" in checklist_content
    assert "Phase 5: Demo Package & Asset Verification" in checklist_content
    assert "Phase 6: Security, Fork Isolation & Secret Sanitization" in checklist_content
    assert "Phase 7: Post-Publication Smoke Test" in checklist_content

    # Verify all local markdown links in storyboard exist
    storyboard_dir = storyboard_path.parent
    for relative_link in ["assets/demo-preview.svg", "assets/demo-hero-poster.svg", "../README.md", "RELEASE_CHECKLIST.md", "../benchmarks/results.md", "../action.yml"]:
        target = (storyboard_dir / relative_link).resolve()
        assert target.exists(), f"Link target {target} does not exist"

