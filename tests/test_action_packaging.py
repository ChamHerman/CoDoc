from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

from codoc.action import ActionConfig, ActionContext, run_action


def test_action_manifest_exists_and_valid() -> None:
    action_yml_path = Path("action.yml")
    assert action_yml_path.is_file(), "action.yml manifest must exist in repository root"
    content = action_yml_path.read_text(encoding="utf-8")

    # Verify essential fields
    assert "name:" in content
    assert "description:" in content
    assert "runs:" in content
    assert "using: 'docker'" in content or 'using: "docker"' in content
    assert "image: 'Dockerfile'" in content or 'image: "Dockerfile"' in content

    # Verify all expected inputs
    expected_inputs = [
        "github_token",
        "llm_api_key",
        "llm_model",
        "confidence_threshold",
        "max_suspects",
        "ignore_paths",
        "auto_merge",
        "fail_on_stale",
        "base_url",
        "embedding_api_key",
        "embedding_model",
    ]
    for inp in expected_inputs:
        assert f"{inp}:" in content, f"Input '{inp}' missing from action.yml"

    # Verify all expected outputs
    expected_outputs = [
        "verified_accurate",
        "auto_fixed",
        "flagged_for_review",
        "unlinked_new_symbols",
        "stale_count",
        "fix_pr_url",
        "report_json",
    ]
    for outp in expected_outputs:
        assert f"{outp}:" in content, f"Output '{outp}' missing from action.yml"

    # Verify default consistency with ActionConfig
    cfg = ActionConfig()
    assert str(cfg.confidence_threshold) in content
    assert str(cfg.max_suspects) in content
    assert cfg.llm_model in content


def test_dockerfile_structure() -> None:
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.is_file(), "Dockerfile must exist in repository root"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in content
    assert "ENTRYPOINT" in content
    assert "codoc.action" in content


def test_entrypoint_execution_smoke(tmp_path: Path) -> None:
    # Run python -m codoc.action with loop-guard event payload to verify CLI invocation
    event_file = tmp_path / "event.json"
    event_payload = {
        "pull_request": {
            "number": 99,
            "head": {"ref": "codoc/fix-pr-99", "repo": {"full_name": "owner/repo"}},
            "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        },
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "github-actions[bot]"},
    }
    event_file.write_text(json.dumps(event_payload), encoding="utf-8")
    output_file = tmp_path / "output"

    env = {
        **dict(os.environ),
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_ACTOR": "github-actions[bot]",
        "GITHUB_OUTPUT": str(output_file),
        "PYTHONPATH": "src",
    }

    result = subprocess.run(
        [sys.executable, "-m", "codoc.action"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Skipping CoDoc run" in result.stdout
