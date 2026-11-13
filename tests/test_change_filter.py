from __future__ import annotations

from codoc.change_detector import (
    find_unlinked_new_symbols,
    is_public_chunk,
)
from codoc.diff_parser import (
    extract_code_deltas,
    filter_code_deltas,
)
from codoc.models import (
    DEFAULT_IGNORE_GLOBS,
    CodeChunk,
    CodeDelta,
    DiffContext,
    LinkEdge,
    LinkGraph,
    is_ignored_path,
    sha256_text,
)


def _make_chunk(
    chunk_id: str,
    raw_source: str,
    chunk_type: str = "function",
    symbol_name: str | None = None,
) -> CodeChunk:
    file_path, qname = chunk_id.split("::", 1)
    sname = symbol_name or qname.split(".")[-1]
    return CodeChunk(
        chunk_id=chunk_id,
        file_path=file_path,
        qualified_name=qname,
        symbol_name=sname,
        chunk_type=chunk_type,  # type: ignore[arg-type]
        signature=f"def {sname}(): ...",
        docstring=None,
        start_line=1,
        end_line=len(raw_source.splitlines()),
        raw_source=raw_source,
        content_hash=sha256_text(raw_source),
    )

def test_is_ignored_path_excludes_tests_migrations_custom_globs() -> None:
    assert is_ignored_path("tests/test_foo.py", DEFAULT_IGNORE_GLOBS) is True
    assert is_ignored_path("src/migrations/0001_initial.py", DEFAULT_IGNORE_GLOBS) is True
    assert is_ignored_path("migrations/0002.py", DEFAULT_IGNORE_GLOBS) is True
    assert is_ignored_path("src/core.py", DEFAULT_IGNORE_GLOBS) is False
    custom = DEFAULT_IGNORE_GLOBS + ("generated/**",)
    assert is_ignored_path("generated/client.py", custom) is True

def test_extract_code_deltas_full_file_additions_and_deletions() -> None:
    diff_text = """--- /dev/null
+++ b/src/created.py
@@ -0,0 +1,3 @@
+def created_fn() -> int:
+    return 42
--- a/src/removed.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def removed_fn() -> int:
-    return 100
"""
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/removed.py": "def removed_fn() -> int:\n    return 100\n"},
        head_files={"src/created.py": "def created_fn() -> int:\n    return 42\n"},
    )
    deltas = extract_code_deltas(diff_ctx)
    assert len(deltas) == 2
    by_type = {d.delta_type: d for d in deltas}
    assert by_type["added"].new_chunk is not None
    assert by_type["added"].new_chunk.chunk_id == "src/created.py::created_fn"
    assert by_type["deleted"].old_chunk is not None
    assert by_type["deleted"].old_chunk.chunk_id == "src/removed.py::removed_fn"


def test_ast_normalization_distinguishes_cosmetic_vs_meaningful() -> None:
    # 1. Nested method docstring change only -> skipped
    old_class = """class Greeter:
    def greet(self, name: str) -> str:
        \"\"\"Old docstring.\"\"\"
        return f"Hello {name}"
"""
    new_class = """class Greeter:
    def greet(self, name: str) -> str:
        \"\"\"New expanded docstring for greet.\"\"\"
        # with an extra internal comment
        return f"Hello {name}"
"""
    delta_cosmetic = CodeDelta(
        delta_type="modified",
        old_chunk=_make_chunk("src/greet.py::Greeter", old_class, "class"),
        new_chunk=_make_chunk("src/greet.py::Greeter", new_class, "class"),
    )
    res_cosmetic = filter_code_deltas([delta_cosmetic])
    assert res_cosmetic[0].status == "skipped"

    # 2. Signature shift (param added with default) -> meaningful
    old_sig = "def fetch(url: str) -> str:\n    return ''\n"
    new_sig = "def fetch(url: str, timeout: int = 30) -> str:\n    return ''\n"
    delta_sig = CodeDelta(
        delta_type="modified",
        old_chunk=_make_chunk("src/net.py::fetch", old_sig),
        new_chunk=_make_chunk("src/net.py::fetch", new_sig),
    )
    assert filter_code_deltas([delta_sig])[0].status == "meaningful"

    # 3. Decorator edit -> meaningful
    old_dec = "def handler():\n    pass\n"
    new_dec = "@retry(attempts=3)\ndef handler():\n    pass\n"
    delta_dec = CodeDelta(
        delta_type="modified",
        old_chunk=_make_chunk("src/net.py::handler", old_dec),
        new_chunk=_make_chunk("src/net.py::handler", new_dec),
    )
    assert filter_code_deltas([delta_dec])[0].status == "meaningful"

    # 4. Class base change -> meaningful
    old_base = "class User(BaseModel):\n    pass\n"
    new_base = "class User(BaseModel, AuditMixin):\n    pass\n"
    delta_base = CodeDelta(
        delta_type="modified",
        old_chunk=_make_chunk("src/models.py::User", old_base, "class"),
        new_chunk=_make_chunk("src/models.py::User", new_base, "class"),
    )
    assert filter_code_deltas([delta_base])[0].status == "meaningful"

    # 5. Schema field change -> meaningful
    old_schema = "class Config(BaseModel):\n    port: int = 8000\n"
    new_schema = "class Config(BaseModel):\n    port: int = 8080\n"
    delta_schema = CodeDelta(
        delta_type="modified",
        old_chunk=_make_chunk("src/config.py::Config", old_schema, "schema"),
        new_chunk=_make_chunk("src/config.py::Config", new_schema, "schema"),
    )
    assert filter_code_deltas([delta_schema])[0].status == "meaningful"


def test_find_unlinked_new_symbols_detects_unlinked_public_symbols() -> None:
    pub_chunk = _make_chunk("src/api.py::create_order", "def create_order(): pass", "function")
    priv_chunk = _make_chunk("src/api.py::_internal_helper", "def _internal_helper(): pass", "function")
    linked_chunk = _make_chunk("src/api.py::get_order", "def get_order(): pass", "function")

    deltas = [
        CodeDelta(delta_type="added", old_chunk=None, new_chunk=pub_chunk),
        CodeDelta(delta_type="added", old_chunk=None, new_chunk=priv_chunk),
        CodeDelta(delta_type="added", old_chunk=None, new_chunk=linked_chunk),
        CodeDelta(delta_type="modified", old_chunk=pub_chunk, new_chunk=pub_chunk),
    ]

    edge = LinkEdge(
        source_section_id="docs/api.md#Orders",
        target_chunk_id="src/api.py::get_order",
        link_type="heuristic",
        score=0.9,
    )
    graph = LinkGraph(
        version="1.0",
        code_chunks=(linked_chunk,),
        edges=(edge,),
    )

    unlinked = find_unlinked_new_symbols(deltas, graph)
    assert len(unlinked) == 1
    assert unlinked[0].chunk_id == "src/api.py::create_order"


def test_is_public_ignores_symbols_in_private_folders() -> None:
    priv_mod_chunk = _make_chunk("src/_internal/helper.py::PublicTool", "class PublicTool:\n    pass\n", "class")
    assert priv_mod_chunk.is_public is False


def test_detect_stale_sections_records_unlinked_new_symbols_pipeline() -> None:
    from codoc.change_detector import detect_stale_sections

    class _DummyVerifier:
        def verify_staleness(self, suspect: object) -> object:
            raise NotImplementedError

    diff_text = """--- /dev/null
+++ b/src/catalog.py
@@ -0,0 +1,2 @@
+def list_items() -> list[str]:
+    return []
"""
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        head_files={"src/catalog.py": "def list_items() -> list[str]:\n    return []\n"},
    )
    graph = LinkGraph(version="1.0")
    unlinked_out: list[CodeChunk] = []
    detect_stale_sections(
        diff_context=diff_ctx,
        link_graph=graph,
        llm_client=_DummyVerifier(),  # type: ignore[arg-type]
        unlinked_symbols_out=unlinked_out,
    )
    assert len(unlinked_out) == 1
    assert unlinked_out[0].chunk_id == "src/catalog.py::list_items"
