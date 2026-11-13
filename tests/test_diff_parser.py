from __future__ import annotations

import ast
from codoc.diff_parser import (
    parse_unified_diff,
    extract_code_deltas,
    filter_code_deltas,
)
from codoc.models import CodeChunk, CodeDelta, DiffContext


def test_parse_unified_diff_extracts_modified_files_and_hunk_ranges() -> None:
    diff_text = """diff --git a/src/service.py b/src/service.py
index abc..def 100644
--- a/src/service.py
+++ b/src/service.py
@@ -10,4 +10,5 @@ def process():
     x = 1
-    return x
+    y = 2
+    return x + y
"""
    file_diffs = parse_unified_diff(diff_text)
    assert len(file_diffs) == 1
    fd = file_diffs[0]
    assert fd.old_path == "src/service.py"
    assert fd.new_path == "src/service.py"
    assert fd.old_lines == [11]
    assert fd.new_lines == [11, 12]


def test_extract_code_deltas_pairs_modified_chunks() -> None:
    diff_text = """--- a/src/calc.py
+++ b/src/calc.py
@@ -2,3 +2,3 @@
 def add(a: int, b: int) -> int:
-    return a + b
+    return a + b + 1
"""
    base_calc = """# Calculator
def add(a: int, b: int) -> int:
    return a + b

def unused():
    pass
"""
    head_calc = """# Calculator
def add(a: int, b: int) -> int:
    return a + b + 1

def unused():
    pass
"""
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/calc.py": base_calc},
        head_files={"src/calc.py": head_calc},
    )
    deltas = extract_code_deltas(diff_ctx)
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.delta_type == "modified"
    assert delta.old_chunk is not None
    assert delta.new_chunk is not None
    assert delta.old_chunk.chunk_id == "src/calc.py::add"
    assert delta.new_chunk.chunk_id == "src/calc.py::add"


def test_filter_code_deltas_skips_comment_and_whitespace_changes() -> None:
    diff_text = """--- a/src/calc.py
+++ b/src/calc.py
@@ -2,3 +2,4 @@
 def add(a: int, b: int) -> int:
+    # comment added
     return a + b
"""
    base_calc = """def add(a: int, b: int) -> int:
    return a + b
"""
    head_calc = """def add(a: int, b: int) -> int:
    # comment added
    return a + b
"""
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/calc.py": base_calc},
        head_files={"src/calc.py": head_calc},
    )
    deltas = extract_code_deltas(diff_ctx)
    assert len(deltas) == 1
    results = filter_code_deltas(deltas)
    assert len(results) == 1
    assert results[0].status == "skipped"


def test_filter_code_deltas_retains_meaningful_signature_and_body_changes() -> None:
    diff_text = """--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,2 @@
-def add(a: int) -> int:
-    return a
+def add(a: int, b: int = 0) -> int:
+    return a + b
"""
    base_calc = """def add(a: int) -> int:
    return a
"""
    head_calc = """def add(a: int, b: int = 0) -> int:
    return a + b
"""
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/calc.py": base_calc},
        head_files={"src/calc.py": head_calc},
    )
    deltas = extract_code_deltas(diff_ctx)
    results = filter_code_deltas(deltas)
    assert len(results) == 1
    assert results[0].status == "meaningful"


def test_extract_code_deltas_handles_added_and_deleted_functions() -> None:
    diff_text = """--- /dev/null
+++ b/src/new_file.py
@@ -0,0 +1,2 @@
+def brand_new() -> str:
+    return "hello"
--- a/src/old_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def deleted_fn() -> None:
-    pass
"""
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/old_file.py": "def deleted_fn() -> None:\n    pass\n"},
        head_files={"src/new_file.py": "def brand_new() -> str:\n    return 'hello'\n"},
    )
    deltas = extract_code_deltas(diff_ctx)
    assert len(deltas) == 2
    by_type = {d.delta_type: d for d in deltas}
    assert "added" in by_type
    assert "deleted" in by_type
    assert by_type["added"].chunk_id == "src/new_file.py::brand_new"
    assert by_type["deleted"].chunk_id == "src/old_file.py::deleted_fn"
    results = filter_code_deltas(deltas)
    assert all(r.status == "meaningful" for r in results)
