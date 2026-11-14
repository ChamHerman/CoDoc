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

def build_run_report_comment(
    report: RunReport,
    repo: str = "",
    commit_sha: str = "",
) -> str:
    parts = ["<!-- codoc-report -->"]

    if report.flagged_for_review > 0:
        badge = "⚠️ **Action Required: Review Flags Found**"
    elif report.auto_fixed > 0:
        badge = "🛠️ **Auto-Fixes Available**"
    else:
        badge = "✅ **All Verified Accurate**"

    parts.append(f"### CoDoc Documentation Check: {badge}\n")

    parts.append("| Category | Count |")
    parts.append("| :--- | :--- |")
    parts.append(f"| Verified Accurate | {report.verified_accurate} |")
    parts.append(f"| Auto-Fixed | {report.auto_fixed} |")
    parts.append(f"| Flagged for Review | {report.flagged_for_review} |")
    parts.append(f"| Unlinked New Symbols | {report.unlinked_new_symbols} |")
    if report.truncated_suspects > 0:
        parts.append(f"| Truncated Suspects | {report.truncated_suspects} |")
    parts.append("")

    if report.fix_pr_url:
        parts.append(f"👉 **Automated fixes opened in:** [{report.fix_pr_url}]({report.fix_pr_url})\n")

    if report.review_flags:
        parts.append("### 🚩 Stale Sections Flagged for Manual Review\n")
        for flag in report.review_flags:
            file_path = flag.get("file_path", "")
            heading_path = flag.get("heading_path", "Section")
            line_start = flag.get("line_start", 1)
            line_end = flag.get("line_end", line_start)
            confidence = flag.get("confidence", 0.0)
            diagnosis = flag.get("diagnosis", "Discrepancy detected between code and docs.")
            diff = flag.get("suggested_diff") or flag.get("suggested_patch", "")
            trigger = ", ".join(f"`{s}`" for s in flag.get("trigger_symbols", []))

            if repo and commit_sha:
                link = f"https://github.com/{repo}/blob/{commit_sha}/{file_path}#L{line_start}-L{line_end}"
                title_link = f"[{file_path} > {heading_path}]({link})"
            else:
                title_link = f"`{file_path}` > **{heading_path}** (lines {line_start}–{line_end})"

            parts.append("<details>")
            parts.append(f"<summary><b>{heading_path}</b> in <code>{file_path}</code> (Confidence: {confidence:.0%})</summary>\n")
            parts.append(f"- **Location:** {title_link}")
            if trigger:
                parts.append(f"- **Triggered by:** {trigger}")
            parts.append(f"- **Diagnosis:** {diagnosis}\n")
            if diff:
                parts.append(f"**Suggested Diff:**\n\n{diff}\n")
            parts.append("</details>\n")

    if report.unlinked_symbols_list:
        parts.append("### 📝 Needs New Documentation\n")
        parts.append("The following newly added public code symbols have no matching documentation section:\n")
        parts.append("| Symbol | Defined In | Line |")
        parts.append("| :--- | :--- | :--- |")
        for sym in report.unlinked_symbols_list:
            symbol_name = sym.get("symbol_name", "")
            fpath = sym.get("file_path", "")
            lstart = sym.get("line_start", 1)
            parts.append(f"| `{symbol_name}` | `{fpath}` | {lstart} |")
        parts.append("")

    return "\n".join(parts)


def build_fix_pr_body(
    report: RunReport,
    trigger_pr_number: int | None = None,
) -> str:
    parts = ["## CoDoc Automated Documentation Fixes\n"]
    if trigger_pr_number is not None:
        parts.append(f"This PR applies automated documentation repairs triggered by PR #{trigger_pr_number}.\n")
    else:
        parts.append("This PR applies automated documentation repairs detected by CoDoc.\n")

    parts.append("### Modified Sections\n")
    if report.auto_fix_items:
        for item in report.auto_fix_items:
            fpath = item.get("file_path", "")
            hpath = item.get("heading_path", "")
            triggers = ", ".join(f"`{s}`" for s in item.get("trigger_symbols", []))
            parts.append(f"- **`{fpath}`** > **{hpath}**")
            if triggers:
                parts.append(f"  - Triggered by changes to: {triggers}")
    else:
        parts.append("- Routine documentation updates.")

    parts.append("\n---\n*Generated automatically by CoDoc.*")
    return "\n".join(parts)

def create_or_update_fix_pr(
    ctx: ActionContext,
    client: GitHubClient,
    patched_files: dict[str, str],
    pr_body: str,
    pr_title: str = "docs: automated documentation fixes via CoDoc",
) -> str | None:
    if not patched_files:
        return None

    if ctx.is_fork:
        print(f"::notice::CoDoc running on fork PR #{ctx.pr_number} ({ctx.head_repo}). Skipping auto_fix branch and PR creation.")
        return None

    if not ctx.config.github_token and isinstance(client, DefaultGitHubClient):
        print("::notice::No GitHub token available. Skipping auto_fix branch and PR creation.")
        return None

    if ctx.pr_number is not None:
        branch_name = f"codoc/fix-pr-{ctx.pr_number}"
        target_base = ctx.head_ref
    else:
        branch_name = f"codoc/fix-pr-{ctx.head_ref}"
        target_base = ctx.head_ref

    base_branch = client.get_branch(ctx.repository, target_base)
    base_sha = "HEAD"
    if base_branch and isinstance(base_branch, dict) and "commit" in base_branch:
        base_sha = base_branch["commit"].get("sha", "HEAD")

    try:
        client.create_or_update_branch(ctx.repository, branch_name, base_sha)
    except Exception as exc:
        print(f"::notice::Failed to create/update branch {branch_name}: {exc}")
        return None

    for file_path, content in patched_files.items():
        try:
            client.create_or_update_file(
                ctx.repository,
                branch_name,
                file_path,
                content,
                f"docs: auto-fix {file_path}",
            )
        except Exception as exc:
            print(f"::notice::Failed to push fix for {file_path}: {exc}")
            return None

    # Idempotent PR lookup
    existing_pr = client.find_pull_request(ctx.repository, head=branch_name, base=target_base)
    if existing_pr is not None:
        pr_number = existing_pr["number"]
        try:
            updated = client.update_pull_request(ctx.repository, pr_number, title=pr_title, body=pr_body)
            pr_url = updated.get("html_url") or f"https://github.com/{ctx.repository}/pull/{pr_number}"
        except Exception as exc:
            print(f"::notice::Failed to update existing PR #{pr_number}: {exc}")
            pr_url = f"https://github.com/{ctx.repository}/pull/{pr_number}"
    else:
        try:
            new_pr = client.create_pull_request(
                ctx.repository,
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=target_base,
            )
            pr_number = new_pr["number"]
            pr_url = new_pr.get("html_url") or f"https://github.com/{ctx.repository}/pull/{pr_number}"
        except Exception as exc:
            print(f"::notice::Failed to create fix PR: {exc}")
            return None

    if ctx.config.auto_merge:
        try:
            success = client.enable_auto_merge(ctx.repository, pr_number)
            if not success:
                print(f"::notice::Auto-merge could not be enabled on PR #{pr_number} (permission or repository settings restriction).")
        except Exception as exc:
            print(f"::notice::Auto-merge request failed on PR #{pr_number}: {exc}")

    return pr_url


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
        if not comment_body:
            comment_body = build_run_report_comment(report, repo=ctx.repository, commit_sha=ctx.head_ref)
        elif "<!-- codoc-report -->" not in comment_body:
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
    if ctx.config.fail_on_stale and (report.flagged_for_review > 0 or (report.auto_fixed > 0 and not report.fix_pr_url)):
        print("::error::CoDoc check failed: stale documentation sections remain unresolved.")
        return 1

    return 0
