from __future__ import annotations
from codoc.models import IndexConfig

import subprocess
import sys
import json
from pathlib import Path

from codoc.indexer import build_index


def test_build_index_maps_python_symbols_and_markdown_sections_to_heuristic_link_graph(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "sample_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "billing.py").write_text(
        '''def calculate_discount(amount: float, rate: float = 0.1) -> float:
    """Compute the discounted total."""
    return amount * (1.0 - rate)


class InvoiceService:
    """Handles invoice tax calculation."""

    async def apply_tax(self, subtotal: float, tax_rate: float) -> float:
        """Apply regional tax asynchronously."""
        return subtotal * (1.0 + tax_rate)
''',
        encoding="utf-8",
    )

    (repo / "docs" / "billing.md").write_text(
        """# Billing

Overview of billing operations.

## Discounts

Use `calculate_discount()` to compute promotional pricing.

## Async Taxes

Call `InvoiceService.apply_tax` before finalizing totals.
""",
        encoding="utf-8",
    )

    output_file = tmp_path / "state" / "link_graph.json"
    graph, _ = build_index(repo_root=repo, output_path=output_file)

    assert output_file.exists()
    # Must not write generated index inside git-tracked repo source directories
    assert not any(repo.rglob("link_graph.json"))

    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(graph.code_chunks) == 3
    assert payload["code_chunks"]

    chunks_by_id = {c["chunk_id"]: c for c in payload["code_chunks"]}
    assert "src/billing.py::calculate_discount" in chunks_by_id
    assert "src/billing.py::InvoiceService" in chunks_by_id
    assert "src/billing.py::InvoiceService.apply_tax" in chunks_by_id

    discount_chunk = chunks_by_id["src/billing.py::calculate_discount"]
    assert "def calculate_discount(amount: float, rate: float = 0.1) -> float" in discount_chunk["signature"]
    assert discount_chunk["docstring"] == "Compute the discounted total."
    assert discount_chunk["start_line"] == 1
    assert discount_chunk["end_line"] == 3
    assert len(discount_chunk["content_hash"]) == 64

    tax_chunk = chunks_by_id["src/billing.py::InvoiceService.apply_tax"]
    assert "async def apply_tax" in tax_chunk["signature"]

    sections_by_path = {s["heading_path"]: s for s in payload["doc_sections"]}
    assert "Billing" in sections_by_path
    assert "Billing > Discounts" in sections_by_path
    assert "Billing > Async Taxes" in sections_by_path

    edges = {(e["source_section_id"], e["target_chunk_id"], e["link_type"]) for e in payload["edges"]}
    assert (
        sections_by_path["Billing > Discounts"]["section_id"],
        "src/billing.py::calculate_discount",
        "heuristic",
    ) in edges
    assert (
        sections_by_path["Billing > Async Taxes"]["section_id"],
        "src/billing.py::InvoiceService.apply_tax",
        "heuristic",
    ) in edges


def test_build_index_excludes_ignored_globs_and_ignores_headings_inside_fenced_code_blocks(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / "vendor").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "app" / "core.py").write_text(
        "def sync_inventory(warehouse_id: str) -> int:\n    return 42\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_core.py").write_text(
        "def test_sync_inventory() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    (repo / ".venv" / "lib" / "site_helper.py").write_text(
        "def venv_internal() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (repo / "vendor" / "vendored.py").write_text(
        "def vendored_fn() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (repo / "pkg.egg-info" / "sub").mkdir(parents=True)
    (repo / "pkg.egg-info" / "sub" / "meta.py").write_text(
        "def egg_meta() -> None:\n    pass\n",
        encoding="utf-8",
    )

    (repo / "docs" / "guide.md").write_text(
        """# Inventory Guide

Call `sync_inventory` with a valid warehouse ID.
Inline ```triple_backtick_span``` must not start a fence.


```python
# This is a Python comment, NOT a heading
### Also not a heading
sync_inventory("wh-1")
```

~~~bash
# Shell comment inside tilde fence
echo "done"
~~~

## Troubleshooting

Verify warehouse connectivity before calling `sync_inventory`.
""",
        encoding="utf-8",
    )

    cfg = IndexConfig(
        ignore_globs=IndexConfig().ignore_globs + ("vendor/**",),
    )
    output_file = tmp_path / "out.json"
    graph, _ = build_index(repo_root=repo, config=cfg, output_path=output_file)

    chunk_ids = [c.chunk_id for c in graph.code_chunks]
    assert chunk_ids == ["app/core.py::sync_inventory"]

    heading_paths = [s.heading_path for s in graph.doc_sections]
    assert heading_paths == [
        "Inventory Guide",
        "Inventory Guide > Troubleshooting",
    ]
    guide_section = graph.doc_sections[0]
    assert "# This is a Python comment, NOT a heading" in guide_section.raw_content
    assert "# Shell comment inside tilde fence" in guide_section.raw_content

    linked_targets = {(e.source_section_id, e.target_chunk_id) for e in graph.edges}
    assert (guide_section.section_id, "app/core.py::sync_inventory") in linked_targets
    assert (graph.doc_sections[1].section_id, "app/core.py::sync_inventory") in linked_targets


def test_heuristic_linker_suppresses_ambiguous_bare_names_and_resolves_qualified_refs(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "ambiguous_repo"
    (repo / "app").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "app" / "runner.py").write_text(
        """def run() -> None:
    pass


def init() -> None:
    pass


def get(key: str) -> str:
    return key


def config() -> dict[str, str]:
    return {}


class Worker:
    def run(self) -> None:
        pass
""",
        encoding="utf-8",
    )
    (repo / "app" / "auth.py").write_text(
        """def config() -> dict[str, str]:
    return {"mode": "strict"}
""",
        encoding="utf-8",
    )

    (repo / "docs" / "usage.md").write_text(
        """# Usage

## Bare Common Names

You can call `get`, `run`, `init`, or `config` to interact with the tool.

## Qualified References

Use `Worker.run` to start background tasks and `auth.config` to load auth settings.
""",
        encoding="utf-8",
    )

    output_file = tmp_path / "link_graph.json"
    graph, _ = build_index(repo_root=repo, output_path=output_file)

    sections_by_path = {s.heading_path: s for s in graph.doc_sections}
    bare_id = sections_by_path["Usage > Bare Common Names"].section_id
    qual_id = sections_by_path["Usage > Qualified References"].section_id

    bare_edges = [e for e in graph.edges if e.source_section_id == bare_id]
    assert bare_edges == []

    qual_targets = {e.target_chunk_id for e in graph.edges if e.source_section_id == qual_id}
    assert qual_targets == {
        "app/runner.py::Worker.run",
        "app/auth.py::config",
    }


def test_cli_index_produces_deterministic_json_and_keeps_repo_clean(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "cli_repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "math_ops.py").write_text(
        "def multiply_matrix(a: int, b: int) -> int:\n    return a * b\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text(
        "# Math\n\nUse `multiply_matrix` for matrix products.\n",
        encoding="utf-8",
    )

    out1 = tmp_path / "out1.json"
    out2 = tmp_path / "out2.json"

    res1 = subprocess.run(
        [sys.executable, "-m", "codoc", "index", str(repo), "--output", str(out1)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res1.returncode == 0, res1.stderr

    res2 = subprocess.run(
        [sys.executable, "-m", "codoc", "index", str(repo), "--output", str(out2)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res2.returncode == 0, res2.stderr
    assert out1.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")

    # Omitting --output still succeeds and writes outside repo_root
    repo_files_before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
    res_default = subprocess.run(
        [sys.executable, "-m", "codoc", "index", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_default.returncode == 0, res_default.stderr
    repo_files_after = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*"))
    assert repo_files_after == repo_files_before
    default_written = Path(res_default.stdout.strip())
    assert default_written.exists()
    assert not default_written.is_relative_to(repo)


def test_build_index_extracts_and_links_nested_class_methods(tmp_path: Path) -> None:
    repo = tmp_path / "nested_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "models.py").write_text(
        """class Outer:
    class Inner:
        def validate_token(self, token: str) -> bool:
            return bool(token)
""",
        encoding="utf-8",
    )
    (repo / "docs" / "models.md").write_text(
        "# Models\n\nCall `Outer.Inner.validate_token` before saving.\n",
        encoding="utf-8",
    )

    graph, _ = build_index(repo_root=repo, output_path=tmp_path / "nested.json")
    chunk_ids = {c.chunk_id for c in graph.code_chunks}
    assert chunk_ids == {
        "src/models.py::Outer",
        "src/models.py::Outer.Inner",
        "src/models.py::Outer.Inner.validate_token",
    }
    targets = {e.target_chunk_id for e in graph.edges}
    assert targets == {"src/models.py::Outer.Inner.validate_token"}
