from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

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
    LLMPatchValidator,
    build_validation_prompt,
    create_repair_patch,
    evaluate_validation_verdict,
    parse_validation_verdict_json,
    repair_stale_sections,
)


def _make_test_fixture() -> tuple[StalenessVerdict, RepairPatch]:
    new_chunk = CodeChunk(
        chunk_id="src/pricing.py::calculate_price",
        file_path="src/pricing.py",
        qualified_name="calculate_price",
        symbol_name="calculate_price",
        chunk_type="function",
        signature="def calculate_price(base: float, tax_rate: float = 0.05) -> float",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source="def calculate_price(base: float, tax_rate: float = 0.05) -> float:\n    return base * (1 + tax_rate)\n",
        content_hash="h1",
    )
    delta = CodeDelta(delta_type="modified", old_chunk=new_chunk, new_chunk=new_chunk)
    section = DocSection(
        section_id="docs/pricing.md#Calculation",
        file_path="docs/pricing.md",
        heading_path="Calculation",
        start_line=1,
        end_line=5,
        raw_content="# Calculation\nCalculate the total price with `tax_rate` default of 10%.\n",
        content_hash="hd",
    )
    suspect = SuspectSection(section=section, deltas=(delta,), score=0.9)
    verdict = StalenessVerdict(
        section_id=section.section_id,
        is_stale=True,
        diagnosis="Default tax_rate changed from 0.10 to 0.05 in code.",
        change_complexity="simple",
        confidence=0.95,
        suspect_section=suspect,
    )
    patch = create_repair_patch(
        target_section=section,
        proposed_content="# Calculation\nCalculate the total price with `tax_rate` default of 5%.\n",
        summary="Update default tax_rate to 5%",
    )
    return verdict, patch


def test_evaluate_validation_verdict_requires_all_three_criteria() -> None:
    # 1. All pass
    v_pass = evaluate_validation_verdict(accuracy_ok=True, preservation_ok=True, style_ok=True)
    assert v_pass.passed is True
    assert len(v_pass.issues) == 0

    # 2. Accuracy failure
    v_acc = evaluate_validation_verdict(
        accuracy_ok=False,
        preservation_ok=True,
        style_ok=True,
        issues=["Factual inaccuracy: tax rate stated as 20%."],
    )
    assert v_acc.passed is False
    assert v_acc.accuracy_ok is False
    assert "Factual inaccuracy: tax rate stated as 20%." in v_acc.issues

    # 3. Preservation failure
    v_pres = evaluate_validation_verdict(
        accuracy_ok=True,
        preservation_ok=False,
        style_ok=True,
        issues=["Deleted still-accurate usage examples."],
    )
    assert v_pres.passed is False
    assert v_pres.preservation_ok is False
    assert "Deleted still-accurate usage examples." in v_pres.issues

    # 4. Style failure
    v_style = evaluate_validation_verdict(
        accuracy_ok=True,
        preservation_ok=True,
        style_ok=False,
        issues=["Heading level changed from H2 to H1."],
    )
    assert v_style.passed is False
    assert v_style.style_ok is False
    assert "Heading level changed from H2 to H1." in v_style.issues


def test_build_validation_prompt_includes_necessary_context() -> None:
    verdict, patch = _make_test_fixture()
    prompt = build_validation_prompt(patch, verdict)

    assert "docs/pricing.md" in prompt
    assert "Calculation" in prompt
    assert patch.proposed_content in prompt
    assert "def calculate_price" in prompt
    assert verdict.diagnosis in prompt
    assert "accuracy_ok" in prompt
    assert "preservation_ok" in prompt
    assert "style_ok" in prompt


def test_parse_validation_verdict_json_strict() -> None:
    payload = json.dumps({
        "accuracy_ok": True,
        "preservation_ok": True,
        "style_ok": True,
        "issues": [],
    })
    v = parse_validation_verdict_json(payload)
    assert v.passed is True
    assert v.accuracy_ok is True

    # Fenced JSON with failure
    fail_payload = "```json\n" + json.dumps({
        "accuracy_ok": False,
        "preservation_ok": True,
        "style_ok": False,
        "issues": ["Wrong tax percentage", "Missing bullet list"],
    }) + "\n```"
    v_fail = parse_validation_verdict_json(fail_payload)
    assert v_fail.passed is False
    assert len(v_fail.issues) == 2

    # Null issues handling and string issues handling
    v_null = parse_validation_verdict_json(json.dumps({
        "accuracy_ok": False,
        "preservation_ok": True,
        "style_ok": True,
        "issues": None,
    }))
    assert v_null.passed is False
    assert "Factual inaccuracy against code change." in v_null.issues

    v_str = parse_validation_verdict_json(json.dumps({
        "accuracy_ok": True,
        "preservation_ok": False,
        "style_ok": True,
        "issues": "Single error string",
    }))
    assert v_str.passed is False
    assert "Single error string" in v_str.issues

def test_llm_patch_validator_integration_with_pipeline_routing() -> None:
    verdict, patch = _make_test_fixture()

    class StubGenerator:
        def generate_patch(self, v: StalenessVerdict, config: RepairConfig | None = None) -> RepairPatch:
            return patch

    # 1. Validator passes -> auto_fix
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "accuracy_ok": True,
            "preservation_ok": True,
            "style_ok": True,
            "issues": [],
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp

    validator = LLMPatchValidator(client=mock_client)
    plan = repair_stale_sections(
        stale_verdicts=[verdict],
        generator=StubGenerator(),
        validator=validator,
    )
    assert len(plan.auto_fixes) == 1
    assert len(plan.review_flags) == 0

    # 2. Validator fails on preservation -> downgraded to review_flag with issues
    mock_resp_fail = MagicMock()
    mock_resp_fail.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "accuracy_ok": True,
            "preservation_ok": False,
            "style_ok": True,
            "issues": ["Erased accurate caveat paragraph."],
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp_fail

    plan_fail = repair_stale_sections(
        stale_verdicts=[verdict],
        generator=StubGenerator(),
        validator=validator,
    )
    assert len(plan_fail.auto_fixes) == 0
    assert len(plan_fail.review_flags) == 1
    assert "Erased accurate caveat paragraph." in plan_fail.review_flags[0].rejection_reasons

    # 3. Validator fails on accuracy -> downgraded to review_flag with accuracy issue
    mock_resp_acc = MagicMock()
    mock_resp_acc.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "accuracy_ok": False,
            "preservation_ok": True,
            "style_ok": True,
            "issues": ["Stated tax rate as 50% instead of 5%."],
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp_acc
    plan_acc = repair_stale_sections(
        stale_verdicts=[verdict],
        generator=StubGenerator(),
        validator=validator,
    )
    assert len(plan_acc.auto_fixes) == 0
    assert len(plan_acc.review_flags) == 1
    assert "Stated tax rate as 50% instead of 5%." in plan_acc.review_flags[0].rejection_reasons

    # 4. Validator fails on style -> downgraded to review_flag with style issue
    mock_resp_style = MagicMock()
    mock_resp_style.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "accuracy_ok": True,
            "preservation_ok": True,
            "style_ok": False,
            "issues": ["Changed markdown admonition to plain text."],
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp_style
    plan_style = repair_stale_sections(
        stale_verdicts=[verdict],
        generator=StubGenerator(),
        validator=validator,
    )
    assert len(plan_style.auto_fixes) == 0
    assert len(plan_style.review_flags) == 1
    assert "Changed markdown admonition to plain text." in plan_style.review_flags[0].rejection_reasons
