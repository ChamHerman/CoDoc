from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Literal

ChunkType = Literal[
    "function",
    "async_function",
    "class",
    "method",
    "async_method",
    "endpoint",
    "schema",
    "cli_command",
]
ReferenceKind = Literal[
    "backtick",
    "qualified",
    "endpoint",
    "config_key",
    "cli_command",
]
LinkType = Literal["heuristic", "embedding", "hybrid"]
DeltaType = Literal["added", "modified", "deleted"]
ChangeStatus = Literal["meaningful", "skipped"]
RepairMode = Literal["auto_fix", "review_flag"]
ChangeComplexity = Literal["simple", "complex"]

DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    ".git/**",
    ".venv/**",
    "venv/**",
    "env/**",
    "__pycache__/**",
    "tests/**",
    "test/**",
    "build/**",
    "dist/**",
    ".codoc/**",
    "*.egg-info/**",
    "migrations/**",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_ignored_path(rel_posix_path: str, ignore_globs: tuple[str, ...]) -> bool:
    pure = PurePosixPath(rel_posix_path)
    dir_parts = pure.parts[:-1]
    parents = [p.as_posix() for p in pure.parents if p != PurePosixPath(".")]

    for pattern in ignore_globs:
        clean = pattern.rstrip("/")
        if fnmatch(rel_posix_path, clean):
            return True
        if clean.endswith("/**"):
            prefix = clean[:-3]
            if "/" in prefix:
                if any(fnmatch(parent, prefix) for parent in parents):
                    return True
            else:
                if any(fnmatch(part, prefix) for part in dir_parts):
                    return True
    return False


def iter_repo_files(
    repo_root: Path,
    glob_pattern: str,
    ignore_globs: tuple[str, ...],
) -> Iterator[tuple[Path, str]]:
    for path in sorted(repo_root.rglob(glob_pattern)):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if is_ignored_path(rel, ignore_globs):
            continue
        yield path, rel


@dataclass(frozen=True, slots=True)
class IndexConfig:
    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS
    embedding_threshold: float = 0.50
    embedding_top_k: int = 5


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    file_path: str
    qualified_name: str
    symbol_name: str
    chunk_type: ChunkType
    signature: str
    docstring: str | None
    start_line: int
    end_line: int
    raw_source: str
    content_hash: str
    decorators: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_public(self) -> bool:
        if self.chunk_type in ("endpoint", "schema", "cli_command"):
            return True
        for part in PurePosixPath(self.file_path).parts:
            if part.startswith("_") and not part.startswith("__"):
                return False
        parts = self.qualified_name.split(".")
        return not any(part.startswith("_") for part in parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CodeReference:
    raw: str
    normalized: str
    kind: ReferenceKind

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DocSection:
    section_id: str
    file_path: str
    heading_path: str
    start_line: int
    end_line: int
    raw_content: str
    content_hash: str
    references: tuple[CodeReference, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["references"] = [ref.to_dict() for ref in self.references]
        return data


@dataclass(frozen=True, slots=True)
class LinkEdge:
    source_section_id: str
    target_chunk_id: str
    link_type: LinkType
    score: float
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LinkGraph:
    version: str = "1.0"
    code_chunks: tuple[CodeChunk, ...] = field(default_factory=tuple)
    doc_sections: tuple[DocSection, ...] = field(default_factory=tuple)
    edges: tuple[LinkEdge, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "code_chunks": [c.to_dict() for c in self.code_chunks],
            "doc_sections": [s.to_dict() for s in self.doc_sections],
            "edges": [e.to_dict() for e in self.edges],
        }


@dataclass(frozen=True, slots=True)
class CodeDelta:
    delta_type: DeltaType
    old_chunk: CodeChunk | None = None
    new_chunk: CodeChunk | None = None

    @property
    def chunk_id(self) -> str:
        if self.new_chunk is not None:
            return self.new_chunk.chunk_id
        if self.old_chunk is not None:
            return self.old_chunk.chunk_id
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_type": self.delta_type,
            "old_chunk": self.old_chunk.to_dict() if self.old_chunk is not None else None,
            "new_chunk": self.new_chunk.to_dict() if self.new_chunk is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ChangeFilterResult:
    delta: CodeDelta
    status: ChangeStatus
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta": self.delta.to_dict(),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SuspectSection:
    section: DocSection
    deltas: tuple[CodeDelta, ...]
    score: float = 0.0
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section.to_dict(),
            "deltas": [d.to_dict() for d in self.deltas],
            "score": self.score,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class StalenessVerdict:
    section_id: str
    is_stale: bool
    diagnosis: str
    change_complexity: ChangeComplexity
    confidence: float
    suspect_section: SuspectSection | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "is_stale": self.is_stale,
            "diagnosis": self.diagnosis,
            "change_complexity": self.change_complexity,
            "confidence": self.confidence,
            "suspect_section": (
                self.suspect_section.to_dict()
                if self.suspect_section is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class DiffContext:
    unified_diff: str
    base_files: dict[str, str] = field(default_factory=dict)
    head_files: dict[str, str] = field(default_factory=dict)
    repo_root: Path | None = None


@dataclass(frozen=True, slots=True)
class ChangeDetectorConfig:
    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS
    max_suspects: int = 25


@dataclass(frozen=True, slots=True)
class RepairPatch:
    section_id: str
    doc_path: str
    original_content: str
    proposed_content: str
    unified_diff: str
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "doc_path": self.doc_path,
            "original_content": self.original_content,
            "proposed_content": self.proposed_content,
            "unified_diff": self.unified_diff,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ValidationVerdict:
    passed: bool
    accuracy_ok: bool
    preservation_ok: bool
    style_ok: bool
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "accuracy_ok": self.accuracy_ok,
            "preservation_ok": self.preservation_ok,
            "style_ok": self.style_ok,
            "issues": list(self.issues),
        }


@dataclass(frozen=True, slots=True)
class RepairItem:
    mode: RepairMode
    verdict: StalenessVerdict
    patch: RepairPatch | None = None
    validation: ValidationVerdict | None = None
    rejection_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "verdict": self.verdict.to_dict(),
            "patch": self.patch.to_dict() if self.patch is not None else None,
            "validation": self.validation.to_dict() if self.validation is not None else None,
            "rejection_reasons": list(self.rejection_reasons),
        }
    def format_comment(self) -> str:
        sec = self.verdict.suspect_section.section if self.verdict.suspect_section else None
        doc_path = sec.file_path if sec else (self.patch.doc_path if self.patch else "documentation")
        sec_id = sec.section_id if sec else self.verdict.section_id

        reasons_md = (
            "\n".join(f"- {r}" for r in self.rejection_reasons)
            if self.rejection_reasons
            else "- Review required before merging."
        )

        diff_block = ""
        if self.patch and self.patch.unified_diff.strip():
            diff_block = f"#### Proposed Draft Changes\n```diff\n{self.patch.unified_diff.strip()}\n```\n"

        return (
            f"### ⚠️ Documentation Review Flag\n"
            f"- **File:** `{doc_path}` (Section: `{sec_id}`)\n"
            f"- **Diagnosis:** {self.verdict.diagnosis}\n"
            f"- **Confidence:** {self.verdict.confidence:.2f}\n"
            f"- **Complexity:** `{self.verdict.change_complexity}`\n\n"
            f"#### Review Reasons\n"
            f"{reasons_md}\n\n"
            f"{diff_block}"
        ).strip() + "\n"



@dataclass(frozen=True, slots=True)
class RepairPlan:
    items: tuple[RepairItem, ...] = ()
    patched_files: dict[str, str] = field(default_factory=dict)

    @property
    def auto_fixes(self) -> tuple[RepairItem, ...]:
        return tuple(item for item in self.items if item.mode == "auto_fix")

    @property
    def review_flags(self) -> tuple[RepairItem, ...]:
        return tuple(item for item in self.items if item.mode == "review_flag")

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "patched_files": dict(self.patched_files),
            "auto_fixes_count": len(self.auto_fixes),
            "review_flags_count": len(self.review_flags),
        }


@dataclass(frozen=True, slots=True)
class RepairConfig:
    confidence_threshold: float = 0.80
    max_retries: int = 2


@dataclass(frozen=True, slots=True)
class RunReport:
    verified_accurate: int = 0
    auto_fixed: int = 0
    flagged_for_review: int = 0
    unlinked_new_symbols: int = 0
    fix_pr_url: str | None = None
    report_json: str = ""
    summary_markdown: str = ""
    review_flags: tuple[dict[str, Any], ...] = ()
    unlinked_symbols_list: tuple[dict[str, Any], ...] = ()
    auto_fix_items: tuple[dict[str, Any], ...] = ()
    truncated_suspects: int = 0

    def __post_init__(self) -> None:
        if not self.report_json:
            object.__setattr__(self, "report_json", json.dumps(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_accurate": self.verified_accurate,
            "auto_fixed": self.auto_fixed,
            "flagged_for_review": self.flagged_for_review,
            "unlinked_new_symbols": self.unlinked_new_symbols,
            "fix_pr_url": self.fix_pr_url,
            "summary_markdown": self.summary_markdown,
            "review_flags": list(self.review_flags),
            "unlinked_symbols_list": list(self.unlinked_symbols_list),
            "auto_fix_items": list(self.auto_fix_items),
            "truncated_suspects": self.truncated_suspects,
        }
