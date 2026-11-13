from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import textwrap
from typing import Any

from codoc.code_parser import parse_python_source
from codoc.models import (
    DEFAULT_IGNORE_GLOBS,
    ChangeFilterResult,
    CodeChunk,
    CodeDelta,
    DiffContext,
    is_ignored_path,
)


@dataclass(slots=True)
class FileDiff:
    old_path: str | None = None
    new_path: str | None = None
    old_lines: list[int] = field(default_factory=list)
    new_lines: list[int] = field(default_factory=list)


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
def _parse_diff_header_path(line: str, prefix: str) -> str | None:
    raw_path = line[len(prefix):].strip()
    if raw_path.startswith("a/") or raw_path.startswith("b/"):
        raw_path = raw_path[2:]
    elif raw_path == "/dev/null":
        return None
    return raw_path or None


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    file_diffs: list[FileDiff] = []
    current_diff: FileDiff | None = None
    old_line_cursor = 0
    new_line_cursor = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("--- "):
            current_diff = FileDiff(old_path=_parse_diff_header_path(raw_line, "--- "))
            file_diffs.append(current_diff)
            continue

        if raw_line.startswith("+++ ") and current_diff is not None:
            current_diff.new_path = _parse_diff_header_path(raw_line, "+++ ")
            continue
        hunk_match = _HUNK_RE.match(raw_line)
        if hunk_match and current_diff is not None:
            old_line_cursor = int(hunk_match.group(1))
            new_line_cursor = int(hunk_match.group(3))
            continue

        if current_diff is None:
            continue

        if raw_line.startswith("-"):
            current_diff.old_lines.append(old_line_cursor)
            old_line_cursor += 1
        elif raw_line.startswith("+"):
            current_diff.new_lines.append(new_line_cursor)
            new_line_cursor += 1
        elif raw_line.startswith(" ") or not raw_line:
            old_line_cursor += 1
            new_line_cursor += 1

    return file_diffs


def load_file_content(
    path: str | None,
    in_memory_files: dict[str, str],
    repo_root: Path | None,
) -> str | None:
    if not path:
        return None
    if path in in_memory_files:
        return in_memory_files[path]
    if repo_root is not None:
        file_path = repo_root / path
        if file_path.is_file():
            return file_path.read_text(encoding="utf-8")
    return None


def extract_code_deltas(
    diff_ctx: DiffContext,
    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS,
) -> list[CodeDelta]:
    file_diffs = parse_unified_diff(diff_ctx.unified_diff)
    deltas: list[CodeDelta] = []

    for fd in file_diffs:
        target_path = fd.new_path or fd.old_path
        if not target_path or not target_path.endswith(".py"):
            continue
        if is_ignored_path(target_path, ignore_globs):
            continue

        old_content = load_file_content(
            fd.old_path, diff_ctx.base_files, diff_ctx.repo_root
        )
        new_content = load_file_content(
            fd.new_path, diff_ctx.head_files, diff_ctx.repo_root
        )

        old_chunks = (
            parse_python_source(old_content, fd.old_path or target_path)
            if old_content
            else []
        )
        new_chunks = (
            parse_python_source(new_content, fd.new_path or target_path)
            if new_content
            else []
        )

        old_by_id = {c.chunk_id: c for c in old_chunks}
        new_by_id = {c.chunk_id: c for c in new_chunks}

        touched_old_ids: set[str] = set()
        for chunk in old_chunks:
            if any(chunk.start_line <= line <= chunk.end_line for line in fd.old_lines):
                touched_old_ids.add(chunk.chunk_id)

        touched_new_ids: set[str] = set()
        for chunk in new_chunks:
            if any(chunk.start_line <= line <= chunk.end_line for line in fd.new_lines):
                touched_new_ids.add(chunk.chunk_id)

        all_touched = touched_old_ids | touched_new_ids
        for chunk_id in sorted(all_touched):
            old_c = old_by_id.get(chunk_id)
            new_c = new_by_id.get(chunk_id)

            if old_c is not None and new_c is not None:
                deltas.append(
                    CodeDelta(delta_type="modified", old_chunk=old_c, new_chunk=new_c)
                )
            elif old_c is not None and new_c is None:
                deltas.append(
                    CodeDelta(delta_type="deleted", old_chunk=old_c, new_chunk=None)
                )
            elif old_c is None and new_c is not None:
                deltas.append(
                    CodeDelta(delta_type="added", old_chunk=None, new_chunk=new_c)
                )

    return deltas


def _strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        ):
            if node.body and isinstance(node.body[0], ast.Expr):
                val = node.body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    node.body.pop(0)

def _normalized_ast_code(raw_source: str) -> str:
    try:
        tree = ast.parse(textwrap.dedent(raw_source))
        _strip_docstrings(tree)
        return ast.unparse(tree)
    except Exception:
        # ponytail: fallback to stripped raw text if snippet fails AST parsing; upgrade when full multi-language parser added
        return raw_source.strip()


def filter_code_deltas(deltas: list[CodeDelta]) -> list[ChangeFilterResult]:
    results: list[ChangeFilterResult] = []
    for delta in deltas:
        if delta.delta_type in ("added", "deleted"):
            results.append(
                ChangeFilterResult(
                    delta=delta,
                    status="meaningful",
                    reason=f"symbol {delta.delta_type}",
                )
            )
            continue

        old_c = delta.old_chunk
        new_c = delta.new_chunk
        if old_c is None or new_c is None:
            results.append(
                ChangeFilterResult(
                    delta=delta,
                    status="meaningful",
                    reason="missing chunk pair",
                )
            )
            continue

        if old_c.content_hash == new_c.content_hash:
            results.append(
                ChangeFilterResult(
                    delta=delta,
                    status="skipped",
                    reason="content hash identical",
                )
            )
            continue

        old_norm = _normalized_ast_code(old_c.raw_source)
        new_norm = _normalized_ast_code(new_c.raw_source)

        if old_norm == new_norm:
            results.append(
                ChangeFilterResult(
                    delta=delta,
                    status="skipped",
                    reason="whitespace, comments, or docstrings only",
                )
            )
        else:
            results.append(
                ChangeFilterResult(
                    delta=delta,
                    status="meaningful",
                    reason="executable code or signature changed",
                )
            )

    return results
