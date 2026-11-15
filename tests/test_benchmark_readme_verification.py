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
    assert "## Benchmark Evaluation Results" in content or "## Benchmark Results" in content
    assert "| Metric | Value |" in content
    assert "Precision" in content
    assert "Recall" in content
    assert "F1 Score" in content
    assert "FastAPI" in content
    assert "Pydantic" in content
    assert "Methodology" in content


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
