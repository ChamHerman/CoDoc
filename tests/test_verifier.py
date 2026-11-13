from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from codoc.models import (
    CodeChunk,
    CodeDelta,
    DocSection,
    StalenessVerdict,
    SuspectSection,
)
from codoc.verifier import (
    LLMStalenessVerifier,
    build_verification_prompt,
    parse_staleness_verdict_json,
)


def _make_dummy_suspect() -> SuspectSection:
    old_c = CodeChunk(
        chunk_id="src/math.py::calc",
        file_path="src/math.py",
        qualified_name="calc",
        symbol_name="calc",
        chunk_type="function",
        signature="def calc(x: int) -> int",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source="def calc(x: int) -> int:\n    return x\n",
        content_hash="h1",
    )
    new_c = CodeChunk(
        chunk_id="src/math.py::calc",
        file_path="src/math.py",
        qualified_name="calc",
        symbol_name="calc",
        chunk_type="function",
        signature="def calc(x: int, scale: int = 2) -> int",
        docstring=None,
        start_line=1,
        end_line=2,
        raw_source="def calc(x: int, scale: int = 2) -> int:\n    return x * scale\n",
        content_hash="h2",
    )
    delta = CodeDelta(delta_type="modified", old_chunk=old_c, new_chunk=new_c)
    sec = DocSection(
        section_id="docs/math.md#Calculation",
        file_path="docs/math.md",
        heading_path="Calculation",
        start_line=1,
        end_line=4,
        raw_content="# Calculation\nCall `calc(x)` to get raw value.\n",
        content_hash="hd",
    )
    return SuspectSection(section=sec, deltas=(delta,), score=0.9)


def test_build_verification_prompt_formats_sections_and_deltas() -> None:
    suspect = _make_dummy_suspect()
    prompt = build_verification_prompt(suspect)

    assert "docs/math.md" in prompt
    assert "Calculation" in prompt
    assert "Call `calc(x)` to get raw value." in prompt
    assert "def calc(x: int) -> int:" in prompt
    assert "def calc(x: int, scale: int = 2) -> int:" in prompt
    assert "is_stale" in prompt


def test_parse_staleness_verdict_json_extracts_structured_fields() -> None:
    raw_json = json.dumps({
        "is_stale": True,
        "diagnosis": "Missing scale parameter documentation.",
        "change_complexity": "simple",
        "confidence": 0.95,
    })
    verdict = parse_staleness_verdict_json(raw_json, "sec1")
    assert verdict.section_id == "sec1"
    assert verdict.is_stale is True
    assert verdict.diagnosis == "Missing scale parameter documentation."
    assert verdict.change_complexity == "simple"
    assert verdict.confidence == 0.95

    # Markdown fenced json test
    fenced_json = f"```json\n{raw_json}\n```"
    verdict_fenced = parse_staleness_verdict_json(fenced_json, "sec2")
    assert verdict_fenced.section_id == "sec2"
    assert verdict_fenced.is_stale is True


def test_llm_staleness_verifier_openai_compatible_success() -> None:
    suspect = _make_dummy_suspect()

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "is_stale": True,
        "diagnosis": "Function return value modified without doc update.",
        "change_complexity": "simple",
        "confidence": 0.92,
    })
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    verifier = LLMStalenessVerifier(
        provider="openai",
        api_key="test-key",
        client=mock_client,
    )
    verdict = verifier.verify_staleness(suspect)

    assert verdict.is_stale is True
    assert "Function return value modified" in verdict.diagnosis
    assert verdict.confidence == 0.92
    assert mock_client.chat.completions.create.call_count == 1


def test_llm_staleness_verifier_retries_on_malformed_json_and_succeeds() -> None:
    suspect = _make_dummy_suspect()

    mock_client = MagicMock()
    bad_choice = MagicMock()
    bad_choice.message.content = "Sorry, here is some non-json text."
    bad_response = MagicMock(choices=[bad_choice])

    good_choice = MagicMock()
    good_choice.message.content = json.dumps({
        "is_stale": False,
        "diagnosis": "Accurate.",
        "change_complexity": "simple",
        "confidence": 0.88,
    })
    good_response = MagicMock(choices=[good_choice])

    mock_client.chat.completions.create.side_effect = [bad_response, good_response]

    verifier = LLMStalenessVerifier(
        provider="openai",
        api_key="test-key",
        client=mock_client,
        max_retries=2,
    )
    verdict = verifier.verify_staleness(suspect)

    assert verdict.is_stale is False
    assert mock_client.chat.completions.create.call_count == 2


def test_llm_staleness_verifier_graceful_degradation_after_retries_exhausted() -> None:
    suspect = _make_dummy_suspect()

    mock_client = MagicMock()
    bad_choice = MagicMock()
    bad_choice.message.content = "Invalid garbage"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[bad_choice])

    verifier = LLMStalenessVerifier(
        provider="openai",
        api_key="test-key",
        client=mock_client,
        max_retries=2,
    )
    verdict = verifier.verify_staleness(suspect)

    # 1 initial attempt + 2 retries = 3 attempts total
    assert mock_client.chat.completions.create.call_count == 3
    # Graceful degradation
    assert verdict.is_stale is False
    assert verdict.confidence == 0.0
    assert "malformed" in verdict.diagnosis.lower() or "failed" in verdict.diagnosis.lower()


def test_llm_staleness_verifier_anthropic_adapter() -> None:
    suspect = _make_dummy_suspect()

    mock_anthropic_client = MagicMock()
    mock_msg = MagicMock()
    mock_block = MagicMock()
    mock_block.text = json.dumps({
        "is_stale": True,
        "diagnosis": "Anthropic verdict.",
        "change_complexity": "complex",
        "confidence": 0.85,
    })
    mock_msg.content = [mock_block]
    mock_anthropic_client.messages.create.return_value = mock_msg

    verifier = LLMStalenessVerifier(
        provider="anthropic",
        api_key="sk-ant-test",
        client=mock_anthropic_client,
    )
    verdict = verifier.verify_staleness(suspect)

    assert verdict.is_stale is True
    assert verdict.diagnosis == "Anthropic verdict."
    assert verdict.change_complexity == "complex"
    assert mock_anthropic_client.messages.create.call_count == 1


def test_parse_staleness_verdict_json_strict_validation() -> None:
    # 1. Non-boolean is_stale
    with pytest.raises(ValueError, match="is_stale"):
        parse_staleness_verdict_json(json.dumps({"is_stale": "false", "diagnosis": "x", "change_complexity": "simple", "confidence": 1.0}), "s")

    # 2. Empty or missing diagnosis
    with pytest.raises(ValueError, match="diagnosis"):
        parse_staleness_verdict_json(json.dumps({"is_stale": True, "diagnosis": "  ", "change_complexity": "simple", "confidence": 1.0}), "s")

    # 3. Invalid change_complexity
    with pytest.raises(ValueError, match="change_complexity"):
        parse_staleness_verdict_json(json.dumps({"is_stale": True, "diagnosis": "valid", "change_complexity": "medium", "confidence": 1.0}), "s")

    # 4. Out-of-range confidence
    with pytest.raises(ValueError, match="confidence"):
        parse_staleness_verdict_json(json.dumps({"is_stale": True, "diagnosis": "valid", "change_complexity": "simple", "confidence": 1.5}), "s")


def test_recorded_fixtures_integration() -> None:
    from pathlib import Path
    fixtures_path = Path(__file__).parent / "fixtures" / "staleness_responses.json"
    data = json.loads(fixtures_path.read_text(encoding="utf-8"))

    for name, item in data.items():
        verdict = parse_staleness_verdict_json(item["response"], section_id=f"sec_{name}")
        assert verdict.section_id == f"sec_{name}"
        assert verdict.is_stale == item["expected_is_stale"]
        assert verdict.change_complexity == item["expected_complexity"]
        assert 0.0 <= verdict.confidence <= 1.0
        assert len(verdict.diagnosis) > 0
