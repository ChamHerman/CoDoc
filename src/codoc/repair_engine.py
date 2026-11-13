from __future__ import annotations

import difflib
from typing import Protocol, Sequence

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
