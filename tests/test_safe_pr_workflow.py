from __future__ import annotations

from pathlib import Path

import pytest

from codoc.action import (
    ActionConfig,
    ActionContext,
    create_or_update_fix_pr,
    run_action,
)
from codoc.github import FakeGitHubClient
from codoc.models import RunReport


def test_fix_pr_pull_request_targets_head_branch() -> None:
    client = FakeGitHubClient()
    ctx = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="contributor",
        head_ref="feature-login",
        base_ref="main",
        pr_number=42,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(auto_merge=False),
    )
    patched = {"docs/login.md": "# Login Docs\nUpdated content"}
    body = "## CoDoc Auto-Fix\nUpdated login docs"

    pr_url = create_or_update_fix_pr(ctx, client, patched_files=patched, pr_body=body)

    assert pr_url is not None
    assert "owner/repo/pull/" in pr_url

    # Check branch created with name codoc/fix-pr-42
    expected_branch = "codoc/fix-pr-42"
    assert expected_branch in client.branches["owner/repo"]

    # Check file pushed to branch
    assert client.files["owner/repo"][expected_branch]["docs/login.md"] == "# Login Docs\nUpdated content"

    # Check PR targets head branch feature-login
    prs = client.pull_requests["owner/repo"]
    assert len(prs) == 1
    fix_pr = prs[0]
    assert fix_pr["head"]["ref"] == expected_branch
    assert fix_pr["base"]["ref"] == "feature-login"


def test_fix_pr_push_targets_pushed_branch() -> None:
    client = FakeGitHubClient()
    ctx = ActionContext(
        event_name="push",
        repository="owner/repo",
        actor="committer",
        head_ref="main",
        base_ref="main",
        pr_number=None,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
    )
    patched = {"docs/guide.md": "# Guide\nNew guide"}
    body = "## CoDoc Auto-Fix Push"

    pr_url = create_or_update_fix_pr(ctx, client, patched_files=patched, pr_body=body)

    assert pr_url is not None
    expected_branch = "codoc/fix-pr-main"
    assert expected_branch in client.branches["owner/repo"]
    fix_pr = client.pull_requests["owner/repo"][0]
    assert fix_pr["head"]["ref"] == expected_branch
    assert fix_pr["base"]["ref"] == "main"


def test_fix_pr_idempotent_updates_existing_open_pr() -> None:
    client = FakeGitHubClient()
    ctx = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="contributor",
        head_ref="feature-api",
        base_ref="main",
        pr_number=99,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
    )
    patched_v1 = {"docs/api.md": "v1"}
    pr_url_1 = create_or_update_fix_pr(ctx, client, patched_files=patched_v1, pr_body="Fix v1")
    assert pr_url_1 == "https://github.com/owner/repo/pull/100"
    assert len(client.pull_requests["owner/repo"]) == 1

    # Second run on same branch updates existing PR instead of creating another
    patched_v2 = {"docs/api.md": "v2"}
    pr_url_2 = create_or_update_fix_pr(ctx, client, patched_files=patched_v2, pr_body="Fix v2")
    assert pr_url_2 == pr_url_1
    assert len(client.pull_requests["owner/repo"]) == 1
    assert client.pull_requests["owner/repo"][0]["body"] == "Fix v2"
    assert client.files["owner/repo"]["codoc/fix-pr-99"]["docs/api.md"] == "v2"


def test_fork_degradation_safely_skips_pr_creation() -> None:
    client = FakeGitHubClient()
    ctx = ActionContext(
        event_name="pull_request",
        repository="upstream/repo",
        actor="external-user",
        head_ref="my-patch",
        base_ref="main",
        pr_number=10,
        is_fork=True,
        head_repo="fork/repo",
        base_repo="upstream/repo",
        config=ActionConfig(),
    )
    patched = {"docs/intro.md": "patched"}
    pr_url = create_or_update_fix_pr(ctx, client, patched_files=patched, pr_body="Fix docs")

    # Safe degradation: returns None, no branches or PRs created
    assert pr_url is None
    assert len(client.branches) == 0
    assert len(client.pull_requests) == 0


def test_auto_merge_requested_when_enabled() -> None:
    client = FakeGitHubClient()
    ctx = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="contributor",
        head_ref="feature-x",
        base_ref="main",
        pr_number=50,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(auto_merge=True),
    )
    patched = {"docs/x.md": "patched x"}
    pr_url = create_or_update_fix_pr(ctx, client, patched_files=patched, pr_body="Fix x")

    assert pr_url is not None
    assert len(client.auto_merge_requests) == 1
    assert client.auto_merge_requests[0] == ("owner/repo", 100)


def test_run_action_fork_completes_with_exit_code_0(tmp_path: Path) -> None:
    output_file = tmp_path / "github_output"
    ctx = ActionContext(
        event_name="pull_request",
        repository="upstream/repo",
        actor="external-user",
        head_ref="fork-branch",
        base_ref="main",
        pr_number=5,
        is_fork=True,
        head_repo="fork/repo",
        base_repo="upstream/repo",
        config=ActionConfig(fail_on_stale=False),
        github_output_path=output_file,
    )
    client = FakeGitHubClient()

    def dummy_pipeline(context: ActionContext) -> RunReport:
        return RunReport(
            verified_accurate=2,
            auto_fixed=1,
            flagged_for_review=0,
            unlinked_new_symbols=0,
            fix_pr_url=None,
            summary_markdown="## Fork Run",
        )

    exit_code = run_action(ctx, client=client, pipeline_fn=dummy_pipeline)
    assert exit_code == 0
    # No PR comment on fork
    assert len(client.comments) == 0
