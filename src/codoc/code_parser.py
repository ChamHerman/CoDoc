from __future__ import annotations

import ast
import io
from pathlib import Path
import tokenize
from typing import Any

from codoc.models import (
    DEFAULT_IGNORE_GLOBS,
    ChunkType,
    CodeChunk,
    iter_repo_files,
    sha256_text,
)

_HTTP_METHOD_DECORATORS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "options", "head"}
)
_ROUTE_DECORATORS: frozenset[str] = _HTTP_METHOD_DECORATORS | {"api_route", "route"}
_SCHEMA_BASE_NAMES: frozenset[str] = frozenset(
    {"BaseModel", "BaseSettings", "TypedDict"}
)
_NON_CONFIG_ANNOTATION_SUFFIXES: tuple[str, ...] = (
    "FastAPI",
    "APIRouter",
    "Typer",
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


def _extract_decorators(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> tuple[str, ...]:
    return tuple(ast.unparse(d) for d in node.decorator_list)


def _str_literal(expr: ast.expr | None) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _str_list_literal(expr: ast.expr | None) -> list[str]:
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        out: list[str] = []
        for elt in expr.elts:
            val = _str_literal(elt)
            if val is not None:
                out.append(val)
        return out
    return []


def _collect_router_prefixes(tree: ast.Module) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for stmt in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        func_name = _callable_name(value.func)
        if func_name.endswith("APIRouter"):
            prefix_val: str | None = _str_literal(value.args[0]) if value.args else None
            for kw in value.keywords:
                if kw.arg == "prefix":
                    prefix_val = _str_literal(kw.value)
                    break
            if prefix_val is not None:
                prefixes[target.id] = prefix_val
    return prefixes

def _callable_name(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        parent = _callable_name(expr.value)
        return f"{parent}.{expr.attr}" if parent else expr.attr
    return ""


def _join_route(prefix: str, path: str) -> str:
    if not prefix:
        return path
    clean_prefix = prefix.rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    if clean_path == "/":
        return clean_prefix or "/"
    return f"{clean_prefix}{clean_path}"


def _detect_endpoint_metadata(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    router_prefixes: dict[str, str],
) -> dict[str, Any] | None:
    all_methods: list[str] = []
    routes: list[dict[str, str]] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        method_attr = dec.func.attr
        if method_attr not in _ROUTE_DECORATORS:
            continue
        receiver = _callable_name(dec.func.value)
        route_path: str | None = _str_literal(dec.args[0]) if dec.args else None
        if route_path is None:
            for kw in dec.keywords:
                if kw.arg == "path":
                    route_path = _str_literal(kw.value)
                    break
        if route_path is None:
            continue

        if method_attr in _HTTP_METHOD_DECORATORS:
            methods = [method_attr.upper()]
        else:
            methods = []
            for kw in dec.keywords:
                if kw.arg == "methods":
                    methods = [m.upper() for m in _str_list_literal(kw.value)]
            if not methods:
                methods = ["GET"]

        full_path = _join_route(router_prefixes.get(receiver, ""), route_path)
        for m in methods:
            if m not in all_methods:
                all_methods.append(m)
            routes.append({"method": m, "path": full_path})

    if not routes:
        return None
    return {
        "http_methods": all_methods,
        "route_path": routes[0]["path"],
        "routes": routes,
    }

def _collect_flag_strings(call: ast.Call) -> list[str]:
    flags: list[str] = []
    for arg in call.args:
        val = _str_literal(arg)
        if val is not None and val.startswith("-"):
            flags.append(val)
    for kw in call.keywords:
        if kw.arg == "param_decls":
            for val in _str_list_literal(kw.value):
                if val.startswith("-"):
                    flags.append(val)
    return flags


def _detect_cli_metadata(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, Any] | None:
    command_name: str | None = None
    cli_options: list[str] = []
    subcommands: list[str] = []
    is_cli = False

    for dec in node.decorator_list:
        dec_call = dec if isinstance(dec, ast.Call) else None
        dec_func = dec_call.func if dec_call is not None else dec
        if isinstance(dec_func, ast.Attribute):
            attr = dec_func.attr
            if attr in ("command", "group"):
                is_cli = True
                if dec_call is not None:
                    if dec_call.args:
                        command_name = _str_literal(dec_call.args[0])
                    if command_name is None:
                        for kw in dec_call.keywords:
                            if kw.arg == "name":
                                command_name = _str_literal(kw.value)
                                break
            elif attr in ("option", "argument") and dec_call is not None:
                cli_options.extend(_collect_flag_strings(dec_call))

    # Inspect Typer Option(...) defaults and Annotated[..., typer.Option(...)]
    all_args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
    defaults_pad: list[ast.expr | None] = [None] * (
        len(node.args.posonlyargs) + len(node.args.args) - len(node.args.defaults)
    ) + list(node.args.defaults)
    defaults_all = defaults_pad + list(node.args.kw_defaults)

    for arg, default_expr in zip(all_args, defaults_all):
        candidates: list[ast.expr] = []
        if default_expr is not None:
            candidates.append(default_expr)
        if isinstance(arg.annotation, ast.Subscript):
            if isinstance(arg.annotation.slice, ast.Tuple):
                candidates.extend(arg.annotation.slice.elts)
            else:
                candidates.append(arg.annotation.slice)
        for expr in candidates:
            if isinstance(expr, ast.Call) and _callable_name(expr.func).endswith(
                (".Option", "Option")
            ):
                flags = _collect_flag_strings(expr)
                if flags:
                    cli_options.extend(flags)
                else:
                    cli_options.append(f"--{arg.arg.replace('_', '-')}")

    if is_cli:
        resolved_name = command_name or node.name.replace("_", "-")
        return {
            "command_name": resolved_name,
            "subcommands": [resolved_name],
            "cli_options": list(dict.fromkeys(cli_options)),
        }

    # Detect argparse usage inside function body
    has_argparse = False
    prog_name: str | None = None
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        cname = _callable_name(sub.func)
        if cname.endswith("ArgumentParser"):
            has_argparse = True
            if sub.args:
                prog_name = _str_literal(sub.args[0])
            for kw in sub.keywords:
                if kw.arg == "prog":
                    prog_name = _str_literal(kw.value)
        elif cname.endswith(".add_argument"):
            cli_options.extend(_collect_flag_strings(sub))
        elif cname.endswith(".add_parser") and sub.args:
            has_argparse = True
            sub_name = _str_literal(sub.args[0])
            if sub_name is not None:
                subcommands.append(sub_name)

    if has_argparse:
        resolved_name = prog_name or node.name.replace("_", "-")
        all_cmds = list(dict.fromkeys(([resolved_name] + subcommands)))
        return {
            "command_name": resolved_name,
            "subcommands": all_cmds,
            "cli_options": list(dict.fromkeys(cli_options)),
        }
    return None



def _is_schema_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = _callable_name(base)
        if base_name.rsplit(".", 1)[-1] in _SCHEMA_BASE_NAMES:
            return True
    for dec in node.decorator_list:
        dec_expr = dec.func if isinstance(dec, ast.Call) else dec
        if _callable_name(dec_expr).rsplit(".", 1)[-1] == "dataclass":
            return True
    return False


def _extract_schema_keys(node: ast.ClassDef) -> list[str]:
    keys: list[str] = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if not stmt.target.id.startswith("_"):
                keys.append(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    keys.append(target.id)
    return keys


def _make_schema_constant_chunk(
    node: ast.stmt,
    name: str,
    lines: list[str],
    rel_posix_path: str,
) -> CodeChunk:
    start = node.lineno
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    raw = "\n".join(lines[start - 1 : end])
    return CodeChunk(
        chunk_id=f"{rel_posix_path}::{name}",
        file_path=rel_posix_path,
        qualified_name=name,
        symbol_name=name,
        chunk_type="schema",
        signature=" ".join(raw.split()),
        docstring=None,
        start_line=start,
        end_line=end,
        raw_source=raw,
        content_hash=sha256_text(raw),
        metadata={"config_keys": [name]},
    )


def _extract_module_constant_chunk(
    node: ast.stmt,
    lines: list[str],
    rel_posix_path: str,
) -> CodeChunk | None:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        name = node.target.id
        if name.startswith("_") or name == "TYPE_CHECKING":
            return None
        ann_name = _callable_name(node.annotation)
        if ann_name.endswith(_NON_CONFIG_ANNOTATION_SUFFIXES):
            return None
        is_config_typed = ann_name.rsplit(".", 1)[-1].endswith(
            ("Config", "Settings", "Schema")
        )
        if not name.isupper() and not is_config_typed:
            return None
        return _make_schema_constant_chunk(node, name, lines, rel_posix_path)
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id.isupper()
            and not target.id.startswith("_")
            and isinstance(
                node.value, (ast.Constant, ast.Dict, ast.List, ast.Tuple, ast.Set)
            )
        ):
            return _make_schema_constant_chunk(node, target.id, lines, rel_posix_path)
    return None

def _collect_body_chunks(
    body: list[ast.stmt],
    lines: list[str],
    rel_posix_path: str,
    prefix: str,
    inside_class: bool,
    router_prefixes: dict[str, str],
    chunks: list[CodeChunk],
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_async = isinstance(node, ast.AsyncFunctionDef)
            endpoint_meta = _detect_endpoint_metadata(node, router_prefixes)
            cli_meta = None if endpoint_meta is not None else _detect_cli_metadata(node)
            if endpoint_meta is not None:
                chunk_type: ChunkType = "endpoint"
                node_meta: dict[str, Any] = endpoint_meta
            elif cli_meta is not None:
                chunk_type = "cli_command"
                node_meta = cli_meta
            elif inside_class:
                chunk_type = "async_method" if is_async else "method"
                node_meta = {}
            else:
                chunk_type = "async_function" if is_async else "function"
                node_meta = {}
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
                    decorators=_extract_decorators(node),
                    metadata=node_meta,
                )
            )
        elif isinstance(node, ast.ClassDef):
            c_start, c_end = _node_span(node)
            c_raw = "\n".join(lines[c_start - 1 : c_end])
            c_qname = f"{prefix}.{node.name}" if prefix else node.name
            is_schema = _is_schema_class(node)
            c_type: ChunkType = "schema" if is_schema else "class"
            c_meta: dict[str, Any] = (
                {"config_keys": _extract_schema_keys(node)} if is_schema else {}
            )
            chunks.append(
                CodeChunk(
                    chunk_id=f"{rel_posix_path}::{c_qname}",
                    file_path=rel_posix_path,
                    qualified_name=c_qname,
                    symbol_name=node.name,
                    chunk_type=c_type,
                    signature=_format_class_signature(node, lines),
                    docstring=ast.get_docstring(node),
                    start_line=c_start,
                    end_line=c_end,
                    raw_source=c_raw,
                    content_hash=sha256_text(c_raw),
                    decorators=_extract_decorators(node),
                    metadata=c_meta,
                )
            )
            _collect_body_chunks(
                node.body,
                lines,
                rel_posix_path,
                prefix=c_qname,
                inside_class=True,
                router_prefixes=router_prefixes,
                chunks=chunks,
            )
        elif not inside_class:
            const_chunk = _extract_module_constant_chunk(node, lines, rel_posix_path)
            if const_chunk is not None:
                chunks.append(const_chunk)


def parse_python_source(source: str, rel_posix_path: str) -> list[CodeChunk]:
    tree = ast.parse(source, filename=rel_posix_path)
    lines = source.splitlines()
    router_prefixes = _collect_router_prefixes(tree)
    chunks: list[CodeChunk] = []
    _collect_body_chunks(
        tree.body,
        lines,
        rel_posix_path,
        prefix="",
        inside_class=False,
        router_prefixes=router_prefixes,
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
