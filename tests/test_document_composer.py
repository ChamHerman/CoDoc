from __future__ import annotations

from pathlib import Path
import pytest

from codoc.models import (
    CodeChunk,
    CodeDelta,
    DocSection,
    RepairConfig,
    RepairItem,
    RepairPatch,
    RepairPlan,
    StalenessVerdict,
    SuspectSection,
    ValidationVerdict,
)
from codoc.repair_engine import (
    compose_patched_document,
    compose_patched_files,
    create_repair_patch,
    format_review_flag_comment,
    repair_stale_sections,
)


def _make_section(
    section_id: str,
    file_path: str,
    heading_path: str,
    start_line: int,
    end_line: int,
    raw_content: str,
) -> DocSection:
    return DocSection(
        section_id=section_id,
        file_path=file_path,
        heading_path=heading_path,
        start_line=start_line,
        end_line=end_line,
        raw_content=raw_content,
        content_hash="dummy",
    )


def test_compose_patched_document_multi_section_bottom_up() -> None:
    # 3 sections in one document:
    # Lines 1-4: Section 1
    # Lines 5-8: Section 2
    # Lines 9-12: Section 3
    orig_lines = [
        "# Title",
        "Introductory text.",
        "",
        "## Alpha",
        "Alpha content line 1.",
        "Alpha content line 2.",
        "",
        "## Beta",
        "Beta content line 1.",
        "Beta content line 2.",
        "",
        "## Gamma",
        "Gamma content line 1.",
        "Gamma content line 2.",
    ]
    orig_doc = "\n".join(orig_lines) + "\n"

    sec_alpha = _make_section(
        section_id="doc.md#Alpha",
        file_path="doc.md",
        heading_path="Title > Alpha",
        start_line=4,
        end_line=7,
        raw_content="## Alpha\nAlpha content line 1.\nAlpha content line 2.\n",
    )
    sec_gamma = _make_section(
        section_id="doc.md#Gamma",
        file_path="doc.md",
        heading_path="Title > Gamma",
        start_line=12,
        end_line=14,
        raw_content="## Gamma\nGamma content line 1.\nGamma content line 2.\n",
    )

    # Patch alpha and gamma (different number of lines proposed)
    patch_alpha = create_repair_patch(
        target_section=sec_alpha,
        proposed_content="## Alpha\nAlpha updated with 3 lines.\nExtra line.\n",
    )
    patch_gamma = create_repair_patch(
        target_section=sec_gamma,
        proposed_content="## Gamma\nGamma simplified.\n",
    )

    # Pass in arbitrary order: gamma should be replaced before alpha (bottom-up)
    patched_doc = compose_patched_document(
        original_doc=orig_doc,
        section_patches=[(sec_alpha, patch_alpha), (sec_gamma, patch_gamma)],
    )

    assert "Alpha updated with 3 lines." in patched_doc
    assert "Extra line." in patched_doc
    assert "Beta content line 1." in patched_doc  # Untouched intermediate section intact!
    assert "Gamma simplified." in patched_doc
    assert "Gamma content line 1." not in patched_doc
    assert "Alpha content line 1." not in patched_doc


def test_compose_patched_document_rejects_overlapping_sections() -> None:
    orig_doc = "# Title\nLine 1\nLine 2\nLine 3\n"
    sec1 = _make_section("s1", "doc.md", "T", 1, 3, "# Title\nLine 1\nLine 2\n")
    sec2 = _make_section("s2", "doc.md", "T", 2, 4, "Line 1\nLine 2\nLine 3\n")
    p1 = create_repair_patch(target_section=sec1, proposed_content="P1\n")
    p2 = create_repair_patch(target_section=sec2, proposed_content="P2\n")

    with pytest.raises(ValueError, match="overlapping"):
        compose_patched_document(orig_doc, [(sec1, p1), (sec2, p2)])


def test_compose_patched_document_no_todo_markers() -> None:
    orig_doc = "## Section\nContent\n"
    sec = _make_section("s", "doc.md", "S", 1, 2, orig_doc)
    patch_with_todo = create_repair_patch(
        target_section=sec,
        proposed_content="## Section\n<!-- TODO: check this -->\nNew content\n",
    )
    with pytest.raises(ValueError, match="TODO"):
        compose_patched_document(orig_doc, [(sec, patch_with_todo)])


def test_compose_patched_document_rejects_malformed_line_ranges() -> None:
    orig_doc = "# Title\nLine 1\nLine 2\n"
    # 1. Inverted line range
    sec_inv = _make_section("s_inv", "doc.md", "T", 3, 1, "Line 2\n")
    p_inv = create_repair_patch(target_section=sec_inv, proposed_content="New\n")
    with pytest.raises(ValueError, match="Malformed section line range"):
        compose_patched_document(orig_doc, [(sec_inv, p_inv)])

    # 2. Non-positive line range
    sec_zero = _make_section("s_zero", "doc.md", "T", 0, 2, "Line 1\n")
    p_zero = create_repair_patch(target_section=sec_zero, proposed_content="New\n")
    with pytest.raises(ValueError, match="Malformed section line range"):
        compose_patched_document(orig_doc, [(sec_zero, p_zero)])


def test_compose_patched_document_preserves_crlf_and_no_trailing_newline() -> None:
    # 1. CRLF preservation
    crlf_doc = "# Title\r\n## Sec\r\nOld line.\r\n"
    sec = _make_section("s", "doc.md", "Sec", 2, 3, "## Sec\r\nOld line.\r\n")
    patch = create_repair_patch(target_section=sec, proposed_content="## Sec\r\nNew line.\r\n")
    patched = compose_patched_document(crlf_doc, [(sec, patch)])
    assert "\r\n" in patched
    assert "\n" not in patched.replace("\r\n", "")
    assert patched.endswith("\r\n")

    # 2. No trailing newline preservation
    no_nl_doc = "# Title\n## Sec\nOld line."
    sec_no_nl = _make_section("s", "doc.md", "Sec", 2, 3, "## Sec\nOld line.")
    patch_no_nl = create_repair_patch(target_section=sec_no_nl, proposed_content="## Sec\nNew line.")
    patched_no_nl = compose_patched_document(no_nl_doc, [(sec_no_nl, patch_no_nl)])
    assert not patched_no_nl.endswith("\n")


def test_compose_patched_document_allows_legitimate_todo_in_prose() -> None:
    orig_doc = "## Section\nContent\n"
    sec = _make_section("s", "doc.md", "S", 1, 2, orig_doc)
    # Legitimate mention of "todo" in documentation prose
    patch_legit = create_repair_patch(
        target_section=sec,
        proposed_content="## Section\nWe manage a todo list application for clients.\n",
    )
    result = compose_patched_document(orig_doc, [(sec, patch_legit)])
    assert "todo list application" in result

def test_format_review_flag_comment_contains_diff_and_reasons() -> None:
    sec = _make_section("doc.md#Sec", "doc.md", "Sec", 1, 3, "## Sec\nOld text\n")
    verdict = StalenessVerdict(
        section_id=sec.section_id,
        is_stale=True,
        diagnosis="Complex multi-method architectural change.",
        change_complexity="complex",
        confidence=0.92,
        suspect_section=SuspectSection(section=sec, deltas=()),
    )
    patch = create_repair_patch(
        target_section=sec,
        proposed_content="## Sec\nProposed draft text\n",
        summary="Draft rewrite",
    )
    item = RepairItem(
        mode="review_flag",
        verdict=verdict,
        patch=patch,
        rejection_reasons=("Change complexity is 'complex' (requires human authoring).",),
    )

    comment = format_review_flag_comment(item)
    assert "### ⚠️ Documentation Review Flag" in comment
    assert "doc.md" in comment
    assert "Complex multi-method architectural change." in comment
    assert "Change complexity is 'complex'" in comment
    assert "Proposed Draft Changes" in comment
    assert "+## Sec" in comment or "+Proposed draft text" in comment


def test_repair_stale_sections_populates_patched_files_and_excludes_review_flags() -> None:
    orig_doc = "## Math\nCall `add(a, b)`.\n\n## Advanced\nAdvanced details.\n"
    sec_math = _make_section("math.md#Math", "docs/math.md", "Math", 1, 2, "## Math\nCall `add(a, b)`.\n")
    sec_adv = _make_section("math.md#Adv", "docs/math.md", "Adv", 4, 5, "## Advanced\nAdvanced details.\n")

    v_auto = StalenessVerdict(
        section_id=sec_math.section_id,
        is_stale=True,
        diagnosis="Added default param",
        change_complexity="simple",
        confidence=0.95,
        suspect_section=SuspectSection(section=sec_math, deltas=()),
    )
    v_flag = StalenessVerdict(
        section_id=sec_adv.section_id,
        is_stale=True,
        diagnosis="Deep overhaul",
        change_complexity="complex",
        confidence=0.90,
        suspect_section=SuspectSection(section=sec_adv, deltas=()),
    )

    class StubGen:
        def generate_patch(self, v: StalenessVerdict, config: RepairConfig | None = None) -> RepairPatch:
            if v.section_id == sec_math.section_id:
                return create_repair_patch(target_section=sec_math, proposed_content="## Math\nCall `add(a, b=0)`.\n")
            return create_repair_patch(target_section=sec_adv, proposed_content="## Advanced\nRewritten adv.\n")

    class StubVal:
        def validate_patch(self, p: RepairPatch, v: StalenessVerdict, config: RepairConfig | None = None) -> ValidationVerdict:
            return ValidationVerdict(passed=True, accuracy_ok=True, preservation_ok=True, style_ok=True)

    plan = repair_stale_sections(
        stale_verdicts=[v_auto, v_flag],
        generator=StubGen(),
        validator=StubVal(),
        original_files={"docs/math.md": orig_doc},
    )

    assert len(plan.auto_fixes) == 1
    assert len(plan.review_flags) == 1
    assert "docs/math.md" in plan.patched_files
    patched_file = plan.patched_files["docs/math.md"]

    # Auto fix section applied!
    assert "Call `add(a, b=0)`." in patched_file
    # Review flag section NOT written to committed file!
    assert "Rewritten adv." not in patched_file
    assert "Advanced details." in patched_file  # Original stays intact
