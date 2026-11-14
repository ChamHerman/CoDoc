from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codoc.action import (
    ActionConfig,
    ActionContext,
    build_fix_pr_body,
    build_run_report_comment,
    run_action,
)
from codoc.github import FakeGitHubClient
from codoc.models import RunReport


def test_build_run_report_comment_renders_summary_and_links() -> None:
    report = RunReport(
        verified_accurate=3,
        auto_fixed=1,
        flagged_for_review=1,
        unlinked_new_symbols=2,
        fix_pr_url="https://github.com/owner/repo/pull/101",
        review_flags=(
            {
                "file_path": "docs/auth.md",
                "heading_path": "Authentication > OAuth2",
                "line_start": 40,
                "line_end": 65,
                "diagnosis": "Doc claims endpoint is /v1/auth but code changed to /v2/auth.",
                "confidence": 0.92,
                "suggested_diff": "```diff\n- /v1/auth\n+ /v2/auth\n```",
                "trigger_symbols": ["auth.login"],
            },
        ),
        unlinked_symbols_list=(
            {
                "symbol_name": "billing.create_subscription",
                "file_path": "src/billing.py",
                "line_start": 88,
            },
            {
                "symbol_name": "billing.cancel_subscription",
                "file_path": "src/billing.py",
                "line_start": 120,
            },
        ),
        auto_fix_items=(
            {
                "file_path": "docs/config.md",
                "heading_path": "Configuration > Timeout",
                "trigger_symbols": ["config.DEFAULT_TIMEOUT"],
            },
        ),
    )

    markdown = build_run_report_comment(report, repo="owner/repo", commit_sha="abcdef1")

    # Marker present
    assert "<!-- codoc-report -->" in markdown

    # Summary counts and badge
    assert "Verified accurate: **3**" in markdown or "3 sections verified" in markdown or "| Verified Accurate | 3 |" in markdown
    assert "Auto-fixed: **1**" in markdown or "1 auto-fixed" in markdown or "| Auto-Fixed | 1 |" in markdown
    assert "Flagged for review: **1**" in markdown or "1 flagged" in markdown or "| Flagged for Review | 1 |" in markdown
    assert "Unlinked new symbols: **2**" in markdown or "2 unlinked" in markdown or "| Unlinked New Symbols | 2 |" in markdown

    # Fix PR link
    assert "https://github.com/owner/repo/pull/101" in markdown

    # Clickable link to file
    assert "https://github.com/owner/repo/blob/abcdef1/docs/auth.md#L40-L65" in markdown

    # Collapsible details for review flag
    assert "<details>" in markdown
    assert "<summary>" in markdown
    assert "Authentication > OAuth2" in markdown
    assert "Doc claims endpoint is /v1/auth but code changed to /v2/auth." in markdown
    assert "92%" in markdown or "0.92" in markdown
    assert "```diff" in markdown

    # Needs New Documentation section
    assert "Needs New Documentation" in markdown
    assert "billing.create_subscription" in markdown
    assert "src/billing.py" in markdown


def test_build_fix_pr_body_itemizes_changes() -> None:
    report = RunReport(
        auto_fix_items=(
            {
                "file_path": "docs/cli.md",
                "heading_path": "CLI > Arguments",
                "trigger_symbols": ["cli.main", "cli.parse_args"],
            },
        ),
    )
    body = build_fix_pr_body(report, trigger_pr_number=42)

    assert "## CoDoc Automated Documentation Fixes" in body
    assert "docs/cli.md" in body
    assert "CLI > Arguments" in body
    assert "cli.main" in body
    assert "PR #42" in body


def test_fail_on_stale_policy_gate(tmp_path: Path) -> None:
    output_file = tmp_path / "github_output"
    client = FakeGitHubClient()

    # When fail_on_stale=False (advisory), exits 0 even with flagged items
    ctx_advisory = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="dev",
        head_ref="feat",
        base_ref="main",
        pr_number=1,
        config=ActionConfig(fail_on_stale=False),
        github_output_path=output_file,
    )
    exit_advisory = run_action(
        ctx_advisory,
        client=client,
        pipeline_fn=lambda _: RunReport(verified_accurate=1, flagged_for_review=2),
    )
    assert exit_advisory == 0

    # When fail_on_stale=True, exits 1 if flagged items remain
    ctx_strict = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="dev",
        head_ref="feat",
        base_ref="main",
        pr_number=1,
        config=ActionConfig(fail_on_stale=True),
        github_output_path=output_file,
    )
    exit_strict_fail = run_action(
        ctx_strict,
        client=client,
        pipeline_fn=lambda _: RunReport(verified_accurate=1, flagged_for_review=2),
    )
    assert exit_strict_fail == 1

    # When fail_on_stale=True and 0 flagged items, exits 0
    exit_strict_pass = run_action(
        ctx_strict,
        client=client,
        pipeline_fn=lambda _: RunReport(verified_accurate=3, flagged_for_review=0),
    )
    assert exit_strict_pass == 0
