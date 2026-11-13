from __future__ import annotations

import json
from pathlib import Path
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
)
from codoc.repair_engine import (
    LLMPatchGenerator,
    build_patch_generation_prompt,
    parse_patch_proposal_json,
    parse_validation_verdict_json,
)


def _make_sample_stale_verdict() -> StalenessVerdict:
    old_c = CodeChunk(
        chunk_id="src/api.py::get_items",
        file_path="src/api.py",
        qualified_name="get_items",
        symbol_name="get_items",
        chunk_type="function",
        signature="def get_items(limit: int = 10)",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source="def get_items(limit: int = 10):\n    return []\n",
        content_hash="h1",
    )
    new_c = CodeChunk(
        chunk_id="src/api.py::get_items",
        file_path="src/api.py",
        qualified_name="get_items",
        symbol_name="get_items",
        chunk_type="function",
        signature="def get_items(limit: int = 50)",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source="def get_items(limit: int = 50):\n    return []\n",
        content_hash="h2",
    )
    delta = CodeDelta(delta_type="modified", old_chunk=old_c, new_chunk=new_c)
    sec = DocSection(
        section_id="docs/api.md#Get Items",
        file_path="docs/api.md",
        heading_path="Get Items",
        start_line=1,
        end_line=4,
        raw_content="## Get Items\nFetches items with default limit of 10.\n",
        content_hash="hd",
    )
    suspect = SuspectSection(section=sec, deltas=(delta,), score=0.9)
    return StalenessVerdict(
        section_id=sec.section_id,
        is_stale=True,
        diagnosis="Default limit parameter changed from 10 to 50 in code.",
        change_complexity="simple",
        confidence=0.95,
        suspect_section=suspect,
    )


def test_build_patch_generation_prompt_includes_context_and_instructions() -> None:
    verdict = _make_sample_stale_verdict()
    prompt = build_patch_generation_prompt(
        verdict=verdict,
        surrounding_context="Sibling Section: # Overview",
    )

    assert "docs/api.md" in prompt
    assert "Get Items" in prompt
    assert "Fetches items with default limit of 10." in prompt
    assert "Default limit parameter changed from 10 to 50 in code." in prompt
    assert "def get_items(limit: int = 50):" in prompt
    assert "Sibling Section: # Overview" in prompt
    assert "proposed_content" in prompt
    assert "summary" in prompt


def test_parse_patch_proposal_json_creates_repair_patch() -> None:
    verdict = _make_sample_stale_verdict()
    raw_json = json.dumps({
        "proposed_content": "## Get Items\nFetches items with default limit of 50.\n",
        "summary": "Updated default limit to 50",
    })
    patch = parse_patch_proposal_json(raw_json, verdict)
    assert patch is not None
    assert patch.section_id == "docs/api.md#Get Items"
    assert patch.doc_path == "docs/api.md"
    assert "limit of 50" in patch.proposed_content
    assert patch.summary == "Updated default limit to 50"
    assert "+Fetches items with default limit of 50." in patch.unified_diff


def test_parse_patch_proposal_json_strict_validation() -> None:
    verdict = _make_sample_stale_verdict()

    # Missing proposed_content
    with pytest.raises(ValueError, match="proposed_content"):
        parse_patch_proposal_json(json.dumps({"summary": "no content"}), verdict)

    # Empty proposed_content
    with pytest.raises(ValueError, match="proposed_content"):
        parse_patch_proposal_json(json.dumps({"proposed_content": "   ", "summary": "ok"}), verdict)

    # Invalid non-string summary
    with pytest.raises(ValueError, match="summary"):
        parse_patch_proposal_json(json.dumps({"proposed_content": "ok", "summary": 123}), verdict)


def test_llm_patch_generator_retries_on_malformed_json_and_succeeds() -> None:
    verdict = _make_sample_stale_verdict()

    mock_client = MagicMock()
    bad_resp = MagicMock(choices=[MagicMock(message=MagicMock(content="Invalid non-json"))])
    good_resp = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps({
        "proposed_content": "## Get Items\nFetches items with default limit of 50.\n",
        "summary": "Updated limit to 50",
    })))])

    mock_client.chat.completions.create.side_effect = [bad_resp, good_resp]

    generator = LLMPatchGenerator(client=mock_client, max_retries=2)
    patch = generator.generate_patch(verdict)

    assert patch is not None
    assert "limit of 50" in patch.proposed_content
    assert mock_client.chat.completions.create.call_count == 2


def test_llm_patch_generator_graceful_fallback_after_retries_exhausted() -> None:
    verdict = _make_sample_stale_verdict()

    mock_client = MagicMock()
    bad_resp = MagicMock(choices=[MagicMock(message=MagicMock(content="Still bad response"))])
    mock_client.chat.completions.create.return_value = bad_resp

    generator = LLMPatchGenerator(client=mock_client, max_retries=1)
    patch = generator.generate_patch(verdict)

    # 1 initial + 1 retry = 2 attempts
    assert mock_client.chat.completions.create.call_count == 2
    assert patch is None


def test_llm_patch_generator_passes_surrounding_context() -> None:
    verdict = _make_sample_stale_verdict()
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps({
        "proposed_content": "## Get Items\nNew content\n",
        "summary": "Updated with context",
    })))])

    generator = LLMPatchGenerator(client=mock_client)
    patch = generator.generate_patch(verdict, surrounding_context="Parent Heading: # API Reference")
    assert patch is not None
    called_messages = mock_client.chat.completions.create.call_args[1]["messages"]
    user_prompt = called_messages[1]["content"]
    assert "Parent Heading: # API Reference" in user_prompt


def test_llm_patch_validator_retries_and_graceful_fallback() -> None:
    from codoc.repair_engine import LLMPatchValidator, create_repair_patch
    verdict = _make_sample_stale_verdict()
    sec = verdict.suspect_section.section if verdict.suspect_section else None
    patch = create_repair_patch(target_section=sec, proposed_content="## Proposed\n")

    mock_client = MagicMock()
    bad_resp = MagicMock(choices=[MagicMock(message=MagicMock(content="Garbage validator output"))])
    good_resp = MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps({
        "accuracy_ok": True,
        "preservation_ok": True,
        "style_ok": True,
        "issues": [],
    })))])

    mock_client.chat.completions.create.side_effect = [bad_resp, good_resp]
    validator = LLMPatchValidator(client=mock_client, max_retries=2)
    verdict_res = validator.validate_patch(patch, verdict)
    assert verdict_res.passed is True
    assert mock_client.chat.completions.create.call_count == 2

    # Now test complete retry exhaustion falls back gracefully
    mock_client_fail = MagicMock()
    mock_client_fail.chat.completions.create.return_value = bad_resp
    validator_fail = LLMPatchValidator(client=mock_client_fail, max_retries=1)
    verdict_fail = validator_fail.validate_patch(patch, verdict)
    assert verdict_fail.passed is False
    assert any("malformed" in issue.lower() for issue in verdict_fail.issues)
    assert mock_client_fail.chat.completions.create.call_count == 2


def test_recorded_fixtures_integration() -> None:
    fixtures_path = Path(__file__).parent / "fixtures" / "repair_responses.json"
    data = json.loads(fixtures_path.read_text(encoding="utf-8"))

    verdict = _make_sample_stale_verdict()

    # 1. Generator fixture
    gen_item = data["generator_valid_simple"]
    patch = parse_patch_proposal_json(gen_item["response"], verdict)
    assert patch is not None
    assert patch.summary == gen_item["expected_summary"]
    assert gen_item["expected_content_snippet"] in patch.proposed_content

    # 2. Generator fenced fixture
    fenced_item = data["generator_fenced_json"]
    patch_fenced = parse_patch_proposal_json(fenced_item["response"], verdict)
    assert patch_fenced is not None
    assert patch_fenced.summary == fenced_item["expected_summary"]
    assert fenced_item["expected_content_snippet"] in patch_fenced.proposed_content

    # 3. Validator pass fixture
    val_pass = data["validator_pass"]
    v_pass = parse_validation_verdict_json(val_pass["response"])
    assert v_pass.passed == val_pass["expected_passed"]
    assert len(v_pass.issues) == val_pass["expected_issues_count"]

    # 4. Validator fail fixture
    val_fail = data["validator_fail_issues"]
    v_fail = parse_validation_verdict_json(val_fail["response"])
    assert v_fail.passed == val_fail["expected_passed"]
    assert len(v_fail.issues) == val_fail["expected_issues_count"]
