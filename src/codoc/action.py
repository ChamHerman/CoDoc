from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

from codoc.github import DefaultGitHubClient, GitHubClient
from codoc.models import RunReport


@dataclass(frozen=True, slots=True)
class ActionConfig:
    github_token: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    confidence_threshold: float = 0.80
    max_suspects: int = 25
    ignore_paths: tuple[str, ...] = ("tests/**", ".github/**", "migrations/**")
    auto_merge: bool = False
    fail_on_stale: bool = False
    base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None


@dataclass(frozen=True, slots=True)
class ActionContext:
    event_name: str
    repository: str
    actor: str
    head_ref: str
    base_ref: str
    pr_number: int | None = None
    is_fork: bool = False
    head_repo: str = ""
    base_repo: str = ""
    config: ActionConfig = field(default_factory=ActionConfig)
    github_output_path: Path | None = None
    raw_event: dict[str, Any] = field(default_factory=dict)


def _parse_bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def parse_action_context(environ: dict[str, str] | None = None) -> ActionContext:
    env = os.environ if environ is None else environ

    event_path_str = env.get("GITHUB_EVENT_PATH", "")
    event_payload: dict[str, Any] = {}
    if event_path_str and Path(event_path_str).is_file():
        try:
            with open(event_path_str, "r", encoding="utf-8") as f:
                event_payload = json.load(f)
        except Exception as exc:
            print(f"[warning] Failed to parse GITHUB_EVENT_PATH ({event_path_str}): {exc}", file=sys.stderr)

    event_name = env.get("GITHUB_EVENT_NAME", "pull_request")
    repository = env.get("GITHUB_REPOSITORY", event_payload.get("repository", {}).get("full_name", ""))
    actor = env.get("GITHUB_ACTOR", event_payload.get("sender", {}).get("login", ""))
    github_output_str = env.get("GITHUB_OUTPUT")
    github_output_path = Path(github_output_str) if github_output_str else None

    # Parse config inputs
    token = env.get("INPUT_GITHUB_TOKEN") or env.get("GITHUB_TOKEN", "")
    llm_api_key = env.get("INPUT_LLM_API_KEY") or env.get("OPENAI_API_KEY", "")
    llm_model = env.get("INPUT_LLM_MODEL", "gpt-4o-mini")

    raw_conf = env.get("INPUT_CONFIDENCE_THRESHOLD")
    conf_thresh = float(raw_conf) if raw_conf is not None and raw_conf.strip() else 0.80

    raw_suspects = env.get("INPUT_MAX_SUSPECTS")
    max_suspects = int(raw_suspects) if raw_suspects is not None and raw_suspects.strip() else 25

    ignore_str = env.get("INPUT_IGNORE_PATHS")
    if ignore_str:
        ignore_paths = tuple(p.strip() for p in ignore_str.replace("\n", ",").split(",") if p.strip())
    else:
        ignore_paths = ("tests/**", ".github/**", "migrations/**")

    auto_merge = _parse_bool(env.get("INPUT_AUTO_MERGE"), default=False)
    fail_on_stale = _parse_bool(env.get("INPUT_FAIL_ON_STALE"), default=False)
    base_url = env.get("INPUT_BASE_URL")
    embedding_api_key = env.get("INPUT_EMBEDDING_API_KEY")
    embedding_model = env.get("INPUT_EMBEDDING_MODEL")

    config = ActionConfig(
        github_token=token,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        confidence_threshold=conf_thresh,
        max_suspects=max_suspects,
        ignore_paths=ignore_paths,
        auto_merge=auto_merge,
        fail_on_stale=fail_on_stale,
        base_url=base_url,
        embedding_api_key=embedding_api_key,
        embedding_model=embedding_model,
    )

    pr_number: int | None = None
    head_ref = ""
    base_ref = ""
    head_repo = repository
    base_repo = repository
    is_fork = False

    if event_name == "pull_request":
        pr_data = event_payload.get("pull_request", {})
        pr_number = pr_data.get("number") or event_payload.get("number")
        head_data = pr_data.get("head", {})
        base_data = pr_data.get("base", {})
        head_ref = head_data.get("ref", "")
        base_ref = base_data.get("ref", "")
        head_repo = head_data.get("repo", {}).get("full_name", repository)
        base_repo = base_data.get("repo", {}).get("full_name", repository)
        is_fork = bool(head_repo and base_repo and head_repo != base_repo)
    elif event_name == "push":
        ref = event_payload.get("ref", env.get("GITHUB_REF", ""))
        head_ref = ref.removeprefix("refs/heads/")
        base_ref = head_ref
    else:
        ref = event_payload.get("ref", env.get("GITHUB_REF", ""))
        head_ref = ref.removeprefix("refs/heads/")
        base_ref = head_ref

    return ActionContext(
        event_name=event_name,
        repository=repository,
        actor=actor,
        head_ref=head_ref,
        base_ref=base_ref,
        pr_number=pr_number,
        is_fork=is_fork,
        head_repo=head_repo,
        base_repo=base_repo,
        config=config,
        github_output_path=github_output_path,
        raw_event=event_payload,
    )


def check_loop_guard(ctx: ActionContext) -> bool:
    """Returns True if the run originates from a CoDoc branch or bot to prevent infinite CI loops."""
    if ctx.head_ref.startswith("codoc/") or ctx.base_ref.startswith("codoc/"):
        return True
    if ctx.actor.endswith("[bot]") and ("codoc/" in ctx.head_ref or "codoc/" in ctx.base_ref):
        return True
    return False


def write_github_output(output_path: Path, outputs: dict[str, Any]) -> None:
    lines = []
    for key, value in outputs.items():
        if value is None:
            formatted = ""
        elif isinstance(value, bool):
            formatted = "true" if value else "false"
        elif isinstance(value, (int, float)):
            formatted = str(value)
        else:
            formatted = str(value)
        lines.append(f"{key}={formatted}")
    with open(output_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def default_pipeline_fn(ctx: ActionContext) -> RunReport:
    """Stub pipeline for tracer bullet execution."""
    return RunReport(
        verified_accurate=0,
        auto_fixed=0,
        flagged_for_review=0,
        unlinked_new_symbols=0,
        fix_pr_url=None,
        summary_markdown="<!-- codoc-report -->\n## CoDoc Summary\n\nNo issues detected.",
    )


def run_action(
    ctx: ActionContext,
    client: GitHubClient | None = None,
    pipeline_fn: Callable[[ActionContext], RunReport] | None = None,
) -> int:
    # 1. Loop-guard
    if check_loop_guard(ctx):
        print(f"::notice::Skipping CoDoc run: branch '{ctx.head_ref}' or actor '{ctx.actor}' is CoDoc-managed.")
        return 0

    # 2. Client setup
    if client is None:
        client = DefaultGitHubClient(token=ctx.config.github_token, base_url=ctx.config.base_url or "https://api.github.com")

    # 3. Pipeline execution
    runner = pipeline_fn or default_pipeline_fn
    report = runner(ctx)

    # 4. Upsert PR comment if pull request and not fork
    if ctx.pr_number is not None and not ctx.is_fork:
        comment_body = report.summary_markdown
        if "<!-- codoc-report -->" not in comment_body:
            comment_body = f"<!-- codoc-report -->\n{comment_body}"
        try:
            client.upsert_pr_comment(ctx.repository, ctx.pr_number, comment_body)
        except Exception as exc:
            print(f"[warning] Failed to upsert comment on PR #{ctx.pr_number}: {exc}", file=sys.stderr)

    # 5. Emit step outputs
    if ctx.github_output_path:
        outputs: dict[str, Any] = {
            "verified_accurate": report.verified_accurate,
            "auto_fixed": report.auto_fixed,
            "flagged_for_review": report.flagged_for_review,
            "unlinked_new_symbols": report.unlinked_new_symbols,
            "fix_pr_url": report.fix_pr_url or "",
            "report_json": report.report_json,
            "stale_count": report.auto_fixed + report.flagged_for_review,
            "auto_fixed_count": report.auto_fixed,
            "flagged_count": report.flagged_for_review,
            "unlinked_new_count": report.unlinked_new_symbols,
        }
        write_github_output(ctx.github_output_path, outputs)

    # 6. Policy gate
    if ctx.config.fail_on_stale and (report.flagged_for_review > 0 or report.auto_fixed > 0):
        print("::error::CoDoc check failed: stale documentation sections remain unresolved.")
        return 1

    return 0
