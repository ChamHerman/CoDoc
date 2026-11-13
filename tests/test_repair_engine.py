from __future__ import annotations

from codoc.models import (
    CodeChunk,
    CodeDelta,
    DocSection,
    RepairConfig,
    RepairPatch,
    StalenessVerdict,
    SuspectSection,
    ValidationVerdict,
)
from codoc.repair_engine import (
    PatchGenerator,
    PatchValidator,
    create_repair_patch,
    generate_unified_diff,
    repair_stale_sections,
    route_repair_item,
)


def _make_sample_verdict(
    section_id: str = "docs/calc.md#Add",
    complexity: str = "simple",
    confidence: float = 0.9,
    doc_content: str = "# Add\nCall `add(a, b)` to sum numbers.\n",
    old_source: str = "def add(a: int, b: int) -> int:\n    return a + b\n",
    new_source: str = "def add(a: int, b: int = 0) -> int:\n    return a + b\n",
) -> StalenessVerdict:
    old_chunk = CodeChunk(
        chunk_id="src/calc.py::add",
        file_path="src/calc.py",
        qualified_name="add",
        symbol_name="add",
        chunk_type="function",
        signature="def add(a: int, b: int) -> int",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source=old_source,
        content_hash="h1",
    )
    new_chunk = CodeChunk(
        chunk_id="src/calc.py::add",
        file_path="src/calc.py",
        qualified_name="add",
        symbol_name="add",
        chunk_type="function",
        signature="def add(a: int, b: int = 0) -> int",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source=new_source,
        content_hash="h2",
    )
    delta = CodeDelta(delta_type="modified", old_chunk=old_chunk, new_chunk=new_chunk)
    section = DocSection(
        section_id=section_id,
        file_path="docs/calc.md",
        heading_path="Add",
        start_line=1,
        end_line=3,
        raw_content=doc_content,
        content_hash="hd",
    )
    suspect = SuspectSection(section=section, deltas=(delta,), score=0.9)
    return StalenessVerdict(
        section_id=section_id,
        is_stale=True,
        diagnosis="Parameter 'b' now has default value 0.",
        change_complexity=complexity,  # type: ignore[arg-type]
        confidence=confidence,
        suspect_section=suspect,
    )


def test_generate_unified_diff_produces_standard_diff_even_without_trailing_newlines() -> None:
    orig = "Line 1\nLine 2"
    prop = "Line 1\nLine 2 modified"
    diff = generate_unified_diff(orig, prop, "docs/test.md")
    assert "--- a/docs/test.md\n" in diff
    assert "+++ b/docs/test.md\n" in diff
    assert "+Line 2 modified\n" in diff
    assert "-Line 2\n" in diff
    # Ensure lines didn't concatenate into -Line 2+Line 2 modified
    assert "-Line 2+Line 2 modified" not in diff

def test_create_repair_patch_builds_valid_patch_from_doc_section() -> None:
    sec = DocSection(
        section_id="docs/calc.md#Add",
        file_path="docs/calc.md",
        heading_path="Add",
        start_line=1,
        end_line=2,
        raw_content="# Add\nCall `add(a, b)`.\n",
        content_hash="h",
    )
    prop = "# Add\nCall `add(a, b=0)`.\n"
    patch = create_repair_patch(
        target_section=sec,
        proposed_content=prop,
        summary="Update default parameter in doc",
    )
    assert patch.section_id == "docs/calc.md#Add"
    assert patch.doc_path == "docs/calc.md"
    assert patch.original_content == sec.raw_content
    assert patch.proposed_content == prop
    assert patch.summary == "Update default parameter in doc"
    assert "+Call `add(a, b=0)`." in patch.unified_diff

def test_route_repair_item_branches() -> None:
    config = RepairConfig(confidence_threshold=0.80)

    # 1. Simple + high confidence + validated -> auto_fix
    v_simple = _make_sample_verdict(complexity="simple", confidence=0.9)
    patch = create_repair_patch(section_id="s1", doc_path="docs/a.md", original_content="old", proposed_content="new", summary="summary")
    val_pass = ValidationVerdict(passed=True, accuracy_ok=True, preservation_ok=True, style_ok=True)
    item_auto = route_repair_item(v_simple, patch, val_pass, config)
    assert item_auto.mode == "auto_fix"
    assert len(item_auto.rejection_reasons) == 0

    # 2. Simple + low confidence -> review_flag
    v_low = _make_sample_verdict(complexity="simple", confidence=0.75)
    item_low = route_repair_item(v_low, patch, val_pass, config)
    assert item_low.mode == "review_flag"
    assert any("confidence" in r.lower() for r in item_low.rejection_reasons)

    # 3. Complex change -> review_flag
    v_complex = _make_sample_verdict(complexity="complex", confidence=0.95)
    item_complex = route_repair_item(v_complex, patch, val_pass, config)
    assert item_complex.mode == "review_flag"
    assert any("complex" in r.lower() for r in item_complex.rejection_reasons)

    # 4. Validation failed -> review_flag
    val_fail = ValidationVerdict(
        passed=False,
        accuracy_ok=False,
        preservation_ok=True,
        style_ok=True,
        issues=("Factual inaccuracy in parameter type.",),
    )
    item_fail = route_repair_item(v_simple, patch, val_fail, config)
    assert item_fail.mode == "review_flag"
    assert "Factual inaccuracy in parameter type." in item_fail.rejection_reasons

    # 5. Non-stale verdict -> review_flag
    v_not_stale = StalenessVerdict(
        section_id="s1",
        is_stale=False,
        diagnosis="Accurate",
        change_complexity="simple",
        confidence=0.99,
    )
    item_not_stale = route_repair_item(v_not_stale, patch, val_pass, config)
    assert item_not_stale.mode == "review_flag"
    assert any("not stale" in r.lower() for r in item_not_stale.rejection_reasons)

def test_repair_stale_sections_end_to_end_tracer_bullet() -> None:
    from codoc.repair_engine import MockPatchGenerator, MockPatchValidator

    v_auto = _make_sample_verdict(section_id="docs/calc.md#Add", complexity="simple", confidence=0.95)
    v_complex = _make_sample_verdict(section_id="docs/calc.md#Sub", complexity="complex", confidence=0.95)
    v_low_conf = _make_sample_verdict(section_id="docs/calc.md#Mul", complexity="simple", confidence=0.70)
    v_val_fail = _make_sample_verdict(section_id="docs/calc.md#Div", complexity="simple", confidence=0.90)

    fail_val = ValidationVerdict(
        passed=False,
        accuracy_ok=False,
        preservation_ok=True,
        style_ok=True,
        issues=("Factual error in return type.",),
    )

    generator = MockPatchGenerator()
    validator = MockPatchValidator(verdicts={"docs/calc.md#Div": fail_val})

    plan = repair_stale_sections(
        stale_verdicts=[v_auto, v_complex, v_low_conf, v_val_fail],
        generator=generator,
        validator=validator,
    )

    assert len(plan.items) == 4
    assert len(plan.auto_fixes) == 1
    assert plan.auto_fixes[0].verdict.section_id == "docs/calc.md#Add"

    assert len(plan.review_flags) == 3
    flag_by_id = {item.verdict.section_id: item for item in plan.review_flags}
    assert any("complex" in r.lower() for r in flag_by_id["docs/calc.md#Sub"].rejection_reasons)
    assert any("confidence" in r.lower() for r in flag_by_id["docs/calc.md#Mul"].rejection_reasons)
    assert "Factual error in return type." in flag_by_id["docs/calc.md#Div"].rejection_reasons
