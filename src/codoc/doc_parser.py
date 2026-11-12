from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from codoc.models import (
    DEFAULT_IGNORE_GLOBS,
    CodeReference,
    DocSection,
    iter_repo_files,
    sha256_text,
)

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_BACKTICK_FENCE_RE = re.compile(r"^\s{0,3}(`{3,})([^`\n]*)$")
_TILDE_FENCE_RE = re.compile(r"^\s{0,3}(~{3,})(.*)$")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_ENDPOINT_BACKTICK_RE = re.compile(
    r"^(?:(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+)?(/[A-Za-z0-9_.\-/{}:]*)$",
    re.IGNORECASE,
)
_ENDPOINT_PROSE_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(/[A-Za-z0-9_.\-/{}:]+)"
)
_CONFIG_KEY_PROSE_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")
_QUALIFIED_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b")
_CALL_TOKEN_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(?:\(\))?$"
)
_CLI_TOKEN_RE = re.compile(r"^(?:--?[A-Za-z0-9][A-Za-z0-9_-]*|[A-Za-z0-9][A-Za-z0-9_-]*)$")
_CLI_LOWER_CMD_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_CLI_SKIP_WORDS: frozenset[str] = frozenset({"python", "python3", "uv", "run", "pip", "poetry"})


def _extract_cli_tokens(raw: str) -> list[str]:
    parts = raw.split()
    if len(parts) == 1:
        token = parts[0]
        if token.startswith("--") or ("-" in token and _CLI_TOKEN_RE.match(token)):
            return [token]
        return []
    has_flag = any(p.startswith("-") for p in parts)
    lower_cmds = [p for p in parts if _CLI_LOWER_CMD_RE.match(p)]
    # Avoid matching non-CLI phrases like `HTTP POST`
    if not has_flag and len(lower_cmds) < 2:
        return []
    tokens: list[str] = []
    skip_next = False
    for part in parts:
        if skip_next:
            skip_next = False
            continue
        if part == "-m":
            skip_next = True
            continue
        if part in _CLI_SKIP_WORDS:
            continue
        if part.startswith("-") or _CLI_LOWER_CMD_RE.match(part):
            if _CLI_TOKEN_RE.match(part):
                tokens.append(part)
    return tokens


@dataclass(frozen=True, slots=True)
class _FenceState:
    char: str
    length: int


def _normalize_reference(raw: str) -> str | None:
    stripped = raw.strip()
    if not stripped:
        return None
    paren_idx = stripped.find("(")
    candidate = stripped[:paren_idx].strip() if paren_idx != -1 else stripped
    if _CALL_TOKEN_RE.match(candidate):
        return candidate
    return None


def extract_code_references(text: str) -> tuple[CodeReference, ...]:
    refs: list[CodeReference] = []
    seen: set[tuple[str, str]] = set()

    for match in _BACKTICK_RE.finditer(text):
        raw = match.group(1).strip()
        ep_match = _ENDPOINT_BACKTICK_RE.match(raw)
        if ep_match:
            method = ep_match.group(1)
            route_path = ep_match.group(2)
            norm = f"{method.upper()} {route_path}" if method else route_path
            if (norm, "endpoint") not in seen:
                seen.add((norm, "endpoint"))
                refs.append(CodeReference(raw=raw, normalized=norm, kind="endpoint"))
            continue
        cli_tokens = _extract_cli_tokens(raw)
        for token in cli_tokens:
            if (token, "cli_command") not in seen:
                seen.add((token, "cli_command"))
                refs.append(
                    CodeReference(raw=raw, normalized=token, kind="cli_command")
                )
        normalized = _normalize_reference(raw)
        if normalized and (normalized, "backtick") not in seen:
            seen.add((normalized, "backtick"))
            refs.append(CodeReference(raw=raw, normalized=normalized, kind="backtick"))

    text_no_backticks = _BACKTICK_RE.sub(" ", text)
    for match in _ENDPOINT_PROSE_RE.finditer(text_no_backticks):
        norm = f"{match.group(1).upper()} {match.group(2)}"
        if (norm, "endpoint") not in seen:
            seen.add((norm, "endpoint"))
            refs.append(
                CodeReference(
                    raw=f"{match.group(1)} {match.group(2)}",
                    normalized=norm,
                    kind="endpoint",
                )
            )

    for match in _QUALIFIED_RE.finditer(text_no_backticks):
        raw = match.group(1)
        if (raw, "qualified") not in seen and (raw, "backtick") not in seen:
            seen.add((raw, "qualified"))
            refs.append(CodeReference(raw=raw, normalized=raw, kind="qualified"))

    for match in _CONFIG_KEY_PROSE_RE.finditer(text_no_backticks):
        raw = match.group(1)
        if (raw, "config_key") not in seen and (raw, "backtick") not in seen:
            seen.add((raw, "config_key"))
            refs.append(CodeReference(raw=raw, normalized=raw, kind="config_key"))

    return tuple(refs)


def _match_fence(line: str) -> tuple[str, int, str] | None:
    bt = _BACKTICK_FENCE_RE.match(line)
    if bt:
        return ("`", len(bt.group(1)), bt.group(2))
    td = _TILDE_FENCE_RE.match(line)
    if td:
        return ("~", len(td.group(1)), td.group(2))
    return None


def _update_fence_state(
    line: str, active_fence: _FenceState | None
) -> _FenceState | None:
    parsed = _match_fence(line)
    if parsed is None:
        return active_fence
    char, length, info = parsed
    if active_fence is None:
        return _FenceState(char=char, length=length)
    if char == active_fence.char and length >= active_fence.length and info.strip() == "":
        return None
    return active_fence


def parse_markdown_text(markdown: str, rel_posix_path: str) -> list[DocSection]:
    lines = markdown.splitlines()
    if not lines:
        return []

    sections: list[DocSection] = []
    heading_stack: list[tuple[int, str]] = []
    current_start = 1
    current_heading_path = "Document"
    current_lines: list[str] = []
    active_fence: _FenceState | None = None
    seen_section_counts: dict[str, int] = {}

    def flush(end_line: int) -> None:
        trimmed = list(current_lines)
        while trimmed and not trimmed[0].strip():
            trimmed.pop(0)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
            end_line -= 1
        content = "\n".join(trimmed)
        if not content:
            return
        base_id = f"{rel_posix_path}#{current_heading_path}"
        count = seen_section_counts.get(base_id, 0) + 1
        seen_section_counts[base_id] = count
        section_id = base_id if count == 1 else f"{base_id}#{count}"
        sections.append(
            DocSection(
                section_id=section_id,
                file_path=rel_posix_path,
                heading_path=current_heading_path,
                start_line=current_start,
                end_line=end_line,
                raw_content=content,
                content_hash=sha256_text(content),
                references=extract_code_references(content),
            )
        )

    for idx, line in enumerate(lines, start=1):
        new_fence = _update_fence_state(line, active_fence)
        inside_fence = active_fence is not None or new_fence is not None
        if active_fence is None and new_fence is not None:
            active_fence = new_fence
            current_lines.append(line)
            continue
        if active_fence is not None and new_fence is None:
            active_fence = None
            current_lines.append(line)
            continue
        active_fence = new_fence

        if not inside_fence:
            heading_match = _HEADING_RE.match(line)
            if heading_match:
                flush(idx - 1)
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                current_heading_path = " > ".join(t for _, t in heading_stack)
                current_start = idx
                current_lines = [line]
                continue

        current_lines.append(line)

    flush(len(lines))
    return sections


def parse_markdown_repo(
    repo_root: Path,
    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS,
) -> list[DocSection]:
    sections: list[DocSection] = []
    for path, rel in iter_repo_files(repo_root, "*.md", ignore_globs):
        content = path.read_text(encoding="utf-8")
        sections.extend(parse_markdown_text(content, rel))
    return sections
