from __future__ import annotations

import ast
import io
from pathlib import Path
import tokenize

from codoc.models import (
    DEFAULT_IGNORE_GLOBS,
    ChunkType,
    CodeChunk,
    iter_repo_files,
    sha256_text,
)


def _extract_header_from_lines(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    lines: list[str],
) -> str | None:
    max_line = node.body[0].lineno if node.body else (node.end_lineno or node.lineno)
    snippet = "\n".join(lines[node.lineno - 1 : max_line])
    depth = 0
    tokens = tokenize.generate_tokens(io.StringIO(snippet).readline)
    end_pos: tuple[int, int] | None = None
    try:
        for tok in tokens:
            if tok.type == tokenize.OP:
                if tok.string in ("(", "[", "{"):
                    depth += 1
                elif tok.string in (")", "]", "}"):
                    depth = max(0, depth - 1)
                elif tok.string == ":" and depth == 0:
                    end_pos = tok.start
                    break
    except tokenize.TokenError:
        return None
    if end_pos is None:
        return None
    rel_line, col = end_pos
    snippet_lines = snippet.splitlines()
    header_parts = [snippet_lines[idx].strip() for idx in range(rel_line - 1)]
    header_parts.append(snippet_lines[rel_line - 1][:col].strip())
    return " ".join(part for part in header_parts if part)


def _format_function_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
) -> str:
    extracted = _extract_header_from_lines(node, lines)
    if extracted is not None:
        return extracted
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args_str = ast.unparse(node.args)
    ret_str = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({args_str}){ret_str}"


def _format_class_signature(node: ast.ClassDef, lines: list[str]) -> str:
    extracted = _extract_header_from_lines(node, lines)
    if extracted is not None:
        return extracted
    bases = [ast.unparse(b) for b in node.bases]
    for kw in node.keywords:
        if kw.arg is not None:
            bases.append(f"{kw.arg}={ast.unparse(kw.value)}")
        else:
            bases.append(f"**{ast.unparse(kw.value)}")
    if bases:
        return f"class {node.name}({', '.join(bases)})"
    return f"class {node.name}"


def _node_span(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> tuple[int, int]:
    start = node.lineno
    if node.decorator_list:
        start = min(start, *(d.lineno for d in node.decorator_list))
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    return start, end


def _collect_body_chunks(
    body: list[ast.stmt],
    lines: list[str],
    rel_posix_path: str,
    prefix: str,
    inside_class: bool,
    chunks: list[CodeChunk],
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_async = isinstance(node, ast.AsyncFunctionDef)
            if inside_class:
                chunk_type: ChunkType = "async_method" if is_async else "method"
            else:
                chunk_type = "async_function" if is_async else "function"
            start, end = _node_span(node)
            raw = "\n".join(lines[start - 1 : end])
            qname = f"{prefix}.{node.name}" if prefix else node.name
            chunks.append(
                CodeChunk(
                    chunk_id=f"{rel_posix_path}::{qname}",
                    file_path=rel_posix_path,
                    qualified_name=qname,
                    symbol_name=node.name,
                    chunk_type=chunk_type,
                    signature=_format_function_signature(node, lines),
                    docstring=ast.get_docstring(node),
                    start_line=start,
                    end_line=end,
                    raw_source=raw,
                    content_hash=sha256_text(raw),
                )
            )
        elif isinstance(node, ast.ClassDef):
            c_start, c_end = _node_span(node)
            c_raw = "\n".join(lines[c_start - 1 : c_end])
            c_qname = f"{prefix}.{node.name}" if prefix else node.name
            chunks.append(
                CodeChunk(
                    chunk_id=f"{rel_posix_path}::{c_qname}",
                    file_path=rel_posix_path,
                    qualified_name=c_qname,
                    symbol_name=node.name,
                    chunk_type="class",
                    signature=_format_class_signature(node, lines),
                    docstring=ast.get_docstring(node),
                    start_line=c_start,
                    end_line=c_end,
                    raw_source=c_raw,
                    content_hash=sha256_text(c_raw),
                )
            )
            _collect_body_chunks(
                node.body,
                lines,
                rel_posix_path,
                prefix=c_qname,
                inside_class=True,
                chunks=chunks,
            )


def parse_python_source(source: str, rel_posix_path: str) -> list[CodeChunk]:
    tree = ast.parse(source, filename=rel_posix_path)
    lines = source.splitlines()
    chunks: list[CodeChunk] = []
    _collect_body_chunks(
        tree.body,
        lines,
        rel_posix_path,
        prefix="",
        inside_class=False,
        chunks=chunks,
    )
    return chunks


def parse_python_repo(
    repo_root: Path,
    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS,
) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    for path, rel in iter_repo_files(repo_root, "*.py", ignore_globs):
        source = path.read_text(encoding="utf-8")
        try:
            chunks.extend(parse_python_source(source, rel))
        except SyntaxError:
            continue
    return chunks
