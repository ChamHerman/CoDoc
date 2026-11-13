from __future__ import annotations

import difflib
import json
from typing import Any, Protocol, Sequence
from codoc.models import (
    DocSection,
    RepairConfig,
    RepairItem,
    RepairMode,
    RepairPatch,
    RepairPlan,
    StalenessVerdict,
    ValidationVerdict,
)
from codoc.verifier import LLMStalenessVerifier, _extract_json_block


def generate_unified_diff(
    original: str,
    proposed: str,
    file_path: str = "doc.md",
) -> str:
    orig_lines = [f"{line}\n" for line in original.splitlines()]
    prop_lines = [f"{line}\n" for line in proposed.splitlines()]
    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            prop_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )
    return "".join(diff_lines)


def create_repair_patch(
    target_section: DocSection | None = None,
    section_id: str = "",
    doc_path: str = "",
    original_content: str = "",
    proposed_content: str = "",
    summary: str = "",
) -> RepairPatch:
    sec_id = target_section.section_id if target_section is not None else section_id
    path = target_section.file_path if target_section is not None else doc_path
    orig = target_section.raw_content if target_section is not None else original_content
    unified_diff = generate_unified_diff(orig, proposed_content, path)
    return RepairPatch(
        section_id=sec_id,
        doc_path=path,
        original_content=orig,
        proposed_content=proposed_content,
        unified_diff=unified_diff,
        summary=summary,
    )


def route_repair_item(
    verdict: StalenessVerdict,
    patch: RepairPatch | None,
    validation: ValidationVerdict | None,
    config: RepairConfig | None = None,
) -> RepairItem:
    cfg = config or RepairConfig()
    reasons: list[str] = []
    if not verdict.is_stale:
        reasons.append("Verdict is not stale.")

    if patch is None:
        reasons.append("No patch generated.")
    elif patch.proposed_content == patch.original_content:
        reasons.append("Proposed content identical to original content.")

    if verdict.change_complexity != "simple":
        reasons.append(f"Change complexity is '{verdict.change_complexity}' (requires human authoring).")

    if verdict.confidence < cfg.confidence_threshold:
        reasons.append(
            f"Verdict confidence {verdict.confidence:.2f} is below threshold {cfg.confidence_threshold:.2f}."
        )

    if validation is not None:
        if not validation.passed:
            if validation.issues:
                reasons.extend(validation.issues)
            else:
                reasons.append("Validation quality gate failed.")
    elif patch is not None:
        reasons.append("Patch was not validated.")

    is_auto_fix = len(reasons) == 0

    mode: RepairMode = "auto_fix" if is_auto_fix else "review_flag"
    return RepairItem(
        mode=mode,
        verdict=verdict,
        patch=patch,
        validation=validation,
        rejection_reasons=tuple(reasons) if not is_auto_fix else (),
    )


class PatchGenerator(Protocol):
    def generate_patch(
        self,
        verdict: StalenessVerdict,
        config: RepairConfig | None = None,
    ) -> RepairPatch | None:
        ...


class PatchValidator(Protocol):
    def validate_patch(
        self,
        patch: RepairPatch,
        verdict: StalenessVerdict,
        config: RepairConfig | None = None,
    ) -> ValidationVerdict:
        ...


def repair_stale_sections(
    stale_verdicts: Sequence[StalenessVerdict],
    config: RepairConfig | None = None,
    generator: PatchGenerator | None = None,
    validator: PatchValidator | None = None,
) -> RepairPlan:
    cfg = config or RepairConfig()
    items: list[RepairItem] = []

    for verdict in stale_verdicts:
        patch: RepairPatch | None = None
        validation: ValidationVerdict | None = None

        if generator is not None:
            patch = generator.generate_patch(verdict, cfg)

        if patch is not None and validator is not None:
            validation = validator.validate_patch(patch, verdict, cfg)

        item = route_repair_item(
            verdict=verdict,
            patch=patch,
            validation=validation,
            config=cfg,
        )
        items.append(item)

    return RepairPlan(items=tuple(items))


class MockPatchGenerator:
    def __init__(self, patches: dict[str, RepairPatch] | None = None) -> None:
        self.patches = patches or {}
        self.calls: list[StalenessVerdict] = []

    def generate_patch(
        self,
        verdict: StalenessVerdict,
        config: RepairConfig | None = None,
    ) -> RepairPatch | None:
        self.calls.append(verdict)
        if verdict.section_id in self.patches:
            return self.patches[verdict.section_id]
        sec = verdict.suspect_section.section if verdict.suspect_section else None
        orig = sec.raw_content if sec else ""
        doc_path = sec.file_path if sec else "doc.md"
        prop = f"{orig.strip()}\n<!-- auto-update: {verdict.diagnosis} -->\n"
        return create_repair_patch(
            target_section=sec,
            proposed_content=prop,
            summary=f"Update for {verdict.diagnosis}",
            section_id=verdict.section_id,
            doc_path=doc_path,
            original_content=orig,
        )


class MockPatchValidator:
    def __init__(self, verdicts: dict[str, ValidationVerdict] | None = None) -> None:
        self.verdicts = verdicts or {}
        self.calls: list[RepairPatch] = []

    def validate_patch(
        self,
        patch: RepairPatch,
        verdict: StalenessVerdict,
        config: RepairConfig | None = None,
    ) -> ValidationVerdict:
        self.calls.append(patch)
        if patch.section_id in self.verdicts:
            return self.verdicts[patch.section_id]
        return ValidationVerdict(
            passed=True,
            accuracy_ok=True,
            preservation_ok=True,
            style_ok=True,
        )


def evaluate_validation_verdict(
    accuracy_ok: bool,
    preservation_ok: bool,
    style_ok: bool,
    issues: Sequence[str] = (),
) -> ValidationVerdict:
    passed = bool(accuracy_ok and preservation_ok and style_ok)
    final_issues = list(issues)
    if not passed and not final_issues:
        if not accuracy_ok:
            final_issues.append("Factual inaccuracy against code change.")
        if not preservation_ok:
            final_issues.append("Accurate existing explanations or examples were removed.")
        if not style_ok:
            final_issues.append("Style or formatting inconsistency with surrounding document.")
    return ValidationVerdict(
        passed=passed,
        accuracy_ok=accuracy_ok,
        preservation_ok=preservation_ok,
        style_ok=style_ok,
        issues=tuple(final_issues),
    )


def build_validation_prompt(
    patch: RepairPatch,
    verdict: StalenessVerdict,
) -> str:
    deltas_block: list[str] = []
    if verdict.suspect_section is not None:
        for delta in verdict.suspect_section.deltas:
            new_src = (
                delta.new_chunk.raw_source
                if delta.new_chunk is not None
                else "(deleted symbol)"
            )
            deltas_block.append(
                f"### Symbol: {delta.chunk_id}\n```python\n{new_src.strip()}\n```"
            )
    code_context = "\n\n".join(deltas_block) if deltas_block else "(no code deltas)"

    return (
        "You are an independent documentation quality gate verifier.\n"
        "Evaluate whether the proposed documentation patch accurately updates the documentation "
        "while preserving valid existing content and document style.\n\n"
        f"## Target Document: {patch.doc_path} (Section: {patch.section_id})\n"
        f"## Phase 2 Staleness Diagnosis:\n{verdict.diagnosis}\n\n"
        "## Relevant New Code:\n"
        f"{code_context}\n\n"
        "## Original Documentation Content:\n"
        f"```markdown\n{patch.original_content.strip()}\n```\n\n"
        "## Proposed Patched Documentation Content:\n"
        f"```markdown\n{patch.proposed_content.strip()}\n```\n\n"
        "## Verification Criteria:\n"
        "1. accuracy_ok: Is the proposed documentation factually accurate according to the code changes?\n"
        "2. preservation_ok: Does it preserve existing accurate explanations, examples, and warnings?\n"
        "3. style_ok: Does it maintain consistent Markdown headings, formatting, and tone?\n\n"
        "## Output Instructions:\n"
        "Return ONLY a valid JSON object with schema:\n"
        "{\n"
        '  "accuracy_ok": boolean,\n'
        '  "preservation_ok": boolean,\n'
        '  "style_ok": boolean,\n'
        '  "issues": list of strings (reasons for any check that is false)\n'
        "}"
    )


def parse_validation_verdict_json(raw_text: str) -> ValidationVerdict:
    cleaned = _extract_json_block(raw_text)
    data = json.loads(cleaned)

    if not isinstance(data, dict):
        raise ValueError("Validator response did not contain a JSON object.")

    if not isinstance(data.get("accuracy_ok"), bool):
        raise ValueError("Field 'accuracy_ok' must be boolean.")
    if not isinstance(data.get("preservation_ok"), bool):
        raise ValueError("Field 'preservation_ok' must be boolean.")
    if not isinstance(data.get("style_ok"), bool):
        raise ValueError("Field 'style_ok' must be boolean.")

    raw_issues = data.get("issues")
    if raw_issues is None:
        issues = []
    elif isinstance(raw_issues, str):
        issues = [raw_issues.strip()] if raw_issues.strip() else []
    elif isinstance(raw_issues, list):
        issues = [str(i).strip() for i in raw_issues if str(i).strip()]
    else:
        issues = []

    return evaluate_validation_verdict(
        accuracy_ok=data["accuracy_ok"],
        preservation_ok=data["preservation_ok"],
        style_ok=data["style_ok"],
        issues=issues,
    )


class LLMPatchValidator:
    """Independent second-pass LLM validator quality gate for documentation repair patches."""

    def __init__(
        self,
        provider: str = "openai",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
        max_retries: int = 2,
    ) -> None:
        self._llm = LLMStalenessVerifier(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            client=client,
            max_retries=max_retries,
        )

    def validate_patch(
        self,
        patch: RepairPatch,
        verdict: StalenessVerdict,
        config: RepairConfig | None = None,
    ) -> ValidationVerdict:
        prompt = build_validation_prompt(patch, verdict)
        retries = config.max_retries if config else self._llm.max_retries
        attempts = 1 + retries
        last_error = ""

        for _ in range(attempts):
            try:
                if self._llm.provider == "anthropic":
                    raw_text = self._llm._call_anthropic(prompt)
                else:
                    raw_text = self._llm._call_openai(prompt)
                return parse_validation_verdict_json(raw_text)
            except Exception as exc:
                last_error = str(exc)
                continue

        return ValidationVerdict(
            passed=False,
            accuracy_ok=False,
            preservation_ok=False,
            style_ok=False,
            issues=(f"Validation failed due to malformed LLM response: {last_error}",),
        )
