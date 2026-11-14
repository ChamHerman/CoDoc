from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codoc.action import (
    ActionConfig,
    ActionContext,
    check_loop_guard,
    parse_action_context,
    run_action,
    write_github_output,
)
from codoc.github import FakeGitHubClient
from codoc.models import RunReport


def test_run_report_domain_model() -> None:
    report = RunReport(
        verified_accurate=5,
        auto_fixed=2,
        flagged_for_review=1,
        unlinked_new_symbols=3,
        fix_pr_url="https://github.com/owner/repo/pull/10",
        summary_markdown="## Summary",
    )
    assert report.verified_accurate == 5
    assert report.auto_fixed == 2
    assert report.flagged_for_review == 1
    assert report.unlinked_new_symbols == 3
    assert report.fix_pr_url == "https://github.com/owner/repo/pull/10"
    assert report.summary_markdown == "## Summary"
    assert report.report_json != ""
    data = json.loads(report.report_json)
    assert data["verified_accurate"] == 5
    assert data["auto_fixed"] == 2


def test_parse_action_context_pull_request(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    payload = {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "number": 42,
            "head": {
                "ref": "feature-docs",
                "sha": "111111",
                "repo": {"full_name": "owner/repo"},
            },
            "base": {
                "ref": "main",
                "sha": "000000",
                "repo": {"full_name": "owner/repo"},
            },
        },
        "repository": {"full_name": "owner/repo", "default_branch": "main"},
        "sender": {"login": "alice"},
    }
    event_file.write_text(json.dumps(payload), encoding="utf-8")

    environ = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_ACTOR": "alice",
        "INPUT_GITHUB_TOKEN": "secret-token",
        "INPUT_CONFIDENCE_THRESHOLD": "0.85",
        "INPUT_MAX_SUSPECTS": "30",
        "INPUT_AUTO_MERGE": "false",
        "INPUT_FAIL_ON_STALE": "true",
    }

    ctx = parse_action_context(environ)
    assert ctx.event_name == "pull_request"
    assert ctx.repository == "owner/repo"
    assert ctx.actor == "alice"
    assert ctx.pr_number == 42
    assert ctx.head_ref == "feature-docs"
    assert ctx.base_ref == "main"
    assert ctx.is_fork is False
    assert ctx.config.confidence_threshold == 0.85
    assert ctx.config.max_suspects == 30
    assert ctx.config.auto_merge is False
    assert ctx.config.fail_on_stale is True


def test_parse_action_context_push(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    payload = {
        "ref": "refs/heads/main",
        "after": "222222",
        "before": "111111",
        "repository": {"full_name": "owner/repo", "default_branch": "main"},
        "sender": {"login": "bob"},
    }
    event_file.write_text(json.dumps(payload), encoding="utf-8")

    environ = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_ACTOR": "bob",
    }

    ctx = parse_action_context(environ)
    assert ctx.event_name == "push"
    assert ctx.pr_number is None
    assert ctx.head_ref == "main"
    assert ctx.base_ref == "main"


def test_loop_guard_detects_codoc_branch() -> None:
    # PR on codoc branch
    ctx_pr = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="github-actions[bot]",
        head_ref="codoc/fix-pr-42",
        base_ref="main",
        pr_number=43,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
    )
    assert check_loop_guard(ctx_pr) is True

    # Push on codoc branch
    ctx_push = ActionContext(
        event_name="push",
        repository="owner/repo",
        actor="github-actions[bot]",
        head_ref="codoc/fix-pr-10",
        base_ref="codoc/fix-pr-10",
        pr_number=None,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
    )
    assert check_loop_guard(ctx_push) is True

    # Normal PR
    ctx_normal = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="developer",
        head_ref="feature/my-feature",
        base_ref="main",
        pr_number=44,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
    )
    assert check_loop_guard(ctx_normal) is False


def test_github_client_upsert_comment() -> None:
    client = FakeGitHubClient()
    repo = "owner/repo"
    pr = 42

    # First upsert creates comment
    res1 = client.upsert_pr_comment(repo, pr, "<!-- codoc-report -->\nFirst run")
    assert res1["id"] == 1
    assert len(client.comments[repo][pr]) == 1

    # Second upsert updates existing comment with marker
    res2 = client.upsert_pr_comment(repo, pr, "<!-- codoc-report -->\nSecond run")
    assert res2["id"] == 1
    assert len(client.comments[repo][pr]) == 1
    assert client.comments[repo][pr][0]["body"] == "<!-- codoc-report -->\nSecond run"

    # Non-marker comment is not overwritten
    client.comments[repo][pr].append({"id": 2, "body": "User discussion comment"})
    res3 = client.upsert_pr_comment(repo, pr, "<!-- codoc-report -->\nThird run")
    assert res3["id"] == 1
    assert len(client.comments[repo][pr]) == 2
    assert client.comments[repo][pr][1]["body"] == "User discussion comment"


def test_write_github_output(tmp_path: Path) -> None:
    output_file = tmp_path / "github_output"
    outputs = {
        "verified_accurate": 3,
        "auto_fixed": 1,
        "fix_pr_url": "https://github.com/owner/repo/pull/99",
        "report_json": '{"status": "ok"}',
    }
    write_github_output(output_file, outputs)
    content = output_file.read_text(encoding="utf-8")
    assert "verified_accurate=3\n" in content
    assert "auto_fixed=1\n" in content
    assert "fix_pr_url=https://github.com/owner/repo/pull/99\n" in content
    assert 'report_json={"status": "ok"}\n' in content


def test_run_action_loop_guard_exit_clean(tmp_path: Path) -> None:
    output_file = tmp_path / "github_output"
    ctx = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="github-actions[bot]",
        head_ref="codoc/fix-pr-42",
        base_ref="main",
        pr_number=42,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
        github_output_path=output_file,
    )
    client = FakeGitHubClient()
    exit_code = run_action(ctx, client=client)
    assert exit_code == 0
    # No comment created on loop guard skip
    assert len(client.comments) == 0


def test_run_action_end_to_end_tracer(tmp_path: Path) -> None:
    output_file = tmp_path / "github_output"
    ctx = ActionContext(
        event_name="pull_request",
        repository="owner/repo",
        actor="alice",
        head_ref="docs-patch",
        base_ref="main",
        pr_number=7,
        is_fork=False,
        head_repo="owner/repo",
        base_repo="owner/repo",
        config=ActionConfig(),
        github_output_path=output_file,
    )
    client = FakeGitHubClient()

    # Pass a dummy pipeline runner that returns a sample RunReport
    def dummy_pipeline(context: ActionContext) -> RunReport:
        return RunReport(
            verified_accurate=4,
            auto_fixed=1,
            flagged_for_review=0,
            unlinked_new_symbols=2,
            fix_pr_url=None,
            summary_markdown="## CoDoc Results\nAll good.",
        )

    exit_code = run_action(ctx, client=client, pipeline_fn=dummy_pipeline)
    assert exit_code == 0

    # Comment was upserted with marker
    comments = client.comments["owner/repo"][7]
    assert len(comments) == 1
    assert "<!-- codoc-report -->" in comments[0]["body"]
    assert "## CoDoc Results" in comments[0]["body"]

    # Outputs written to GITHUB_OUTPUT
    output_content = output_file.read_text(encoding="utf-8")
    assert "verified_accurate=4\n" in output_content
    assert "auto_fixed=1\n" in output_content
    assert "unlinked_new_symbols=2\n" in output_content
