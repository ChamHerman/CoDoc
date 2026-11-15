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


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    repo_ref: str
    category: str
    description: str = ""
    base_commit: str = ""
    head_commit: str = ""
    patch: str = ""
    patch_file: str = ""
    expected_stale_sections: tuple[str, ...] = ()
    expected_mode: RepairMode | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "repo_ref": self.repo_ref,
            "category": self.category,
            "description": self.description,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "patch": self.patch,
            "patch_file": self.patch_file,
            "expected_stale_sections": list(self.expected_stale_sections),
            "expected_mode": self.expected_mode,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationCase:
        expected_sections = data.get("expected_stale_sections", ())
        if isinstance(expected_sections, list):
            expected_tuple = tuple(expected_sections)
        elif isinstance(expected_sections, tuple):
            expected_tuple = expected_sections
        else:
            expected_tuple = (str(expected_sections),)

        return cls(
            case_id=data["case_id"],
            repo_ref=data.get("repo_ref", ""),
            category=data.get("category", "general"),
            description=data.get("description", ""),
            base_commit=data.get("base_commit", ""),
            head_commit=data.get("head_commit", ""),
            patch=data.get("patch", ""),
            patch_file=data.get("patch_file", ""),
            expected_stale_sections=expected_tuple,
            expected_mode=data.get("expected_mode"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    case_id: str
    category: str
    expected_stale_sections: tuple[str, ...]
    predicted_stale_sections: tuple[str, ...]
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    repair_mode_correct: bool | None = None
    repair_validation_passed: bool | None = None
    latency_seconds: float = 0.0
    token_usage: dict[str, int] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "expected_stale_sections": list(self.expected_stale_sections),
            "predicted_stale_sections": list(self.predicted_stale_sections),
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "repair_mode_correct": self.repair_mode_correct,
            "repair_validation_passed": self.repair_validation_passed,
            "latency_seconds": self.latency_seconds,
            "token_usage": dict(self.token_usage),
            "diagnostics": dict(self.diagnostics),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationResult:
        return cls(
            case_id=data["case_id"],
            category=data.get("category", "general"),
            expected_stale_sections=tuple(data.get("expected_stale_sections", ())),
            predicted_stale_sections=tuple(data.get("predicted_stale_sections", ())),
            true_positives=data.get("true_positives", 0),
            false_positives=data.get("false_positives", 0),
            false_negatives=data.get("false_negatives", 0),
            precision=data.get("precision", 0.0),
            recall=data.get("recall", 0.0),
            f1=data.get("f1", 0.0),
            repair_mode_correct=data.get("repair_mode_correct"),
            repair_validation_passed=data.get("repair_validation_passed"),
            latency_seconds=data.get("latency_seconds", 0.0),
            token_usage=dict(data.get("token_usage", {})),
            diagnostics=dict(data.get("diagnostics", {})),
            error=data.get("error"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    total_cases: int
    positive_cases: int
    negative_cases: int
    total_true_positives: int
    total_false_positives: int
    total_false_negatives: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    false_positive_rate: float
    repair_success_rate: float = 0.0
    average_latency_seconds: float = 0.0
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    case_results: tuple[EvaluationResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "positive_cases": self.positive_cases,
            "negative_cases": self.negative_cases,
            "total_true_positives": self.total_true_positives,
            "total_false_positives": self.total_false_positives,
            "total_false_negatives": self.total_false_negatives,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "accuracy": self.accuracy,
            "false_positive_rate": self.false_positive_rate,
            "repair_success_rate": self.repair_success_rate,
            "average_latency_seconds": self.average_latency_seconds,
            "total_tokens": self.total_tokens,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "case_results": [r.to_dict() for r in self.case_results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationSummary:
        cases = [EvaluationResult.from_dict(r) for r in data.get("case_results", [])]
        return cls(
            total_cases=data.get("total_cases", len(cases)),
            positive_cases=data.get("positive_cases", 0),
            negative_cases=data.get("negative_cases", 0),
            total_true_positives=data.get("total_true_positives", 0),
            total_false_positives=data.get("total_false_positives", 0),
            total_false_negatives=data.get("total_false_negatives", 0),
            precision=data.get("precision", 0.0),
            recall=data.get("recall", 0.0),
            f1=data.get("f1", 0.0),
            accuracy=data.get("accuracy", 0.0),
            false_positive_rate=data.get("false_positive_rate", 0.0),
            repair_success_rate=data.get("repair_success_rate", 0.0),
            average_latency_seconds=data.get("average_latency_seconds", 0.0),
            total_tokens=data.get("total_tokens", 0),
            total_prompt_tokens=data.get("total_prompt_tokens", 0),
            total_completion_tokens=data.get("total_completion_tokens", 0),
            case_results=tuple(cases),
        )

    def to_markdown(self) -> str:
        lines = [
            "# CoDoc Evaluation Benchmark Summary",
            "",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| Total Cases | {self.total_cases} |",
            f"| Positive (Stale) Cases | {self.positive_cases} |",
            f"| Negative (Control) Cases | {self.negative_cases} |",
            f"| True Positives (TP) | {self.total_true_positives} |",
            f"| False Positives (FP) | {self.total_false_positives} |",
            f"| False Negatives (FN) | {self.total_false_negatives} |",
            f"| **Precision** | **{self.precision:.1%}** |",
            f"| **Recall** | **{self.recall:.1%}** |",
            f"| **F1 Score** | **{self.f1:.1%}** |",
            f"| **Exact Match Accuracy** | **{self.accuracy:.1%}** |",
            f"| False Positive Rate | {self.false_positive_rate:.1%} |",
            f"| Repair Success Rate | {self.repair_success_rate:.1%} |",
            f"| Avg Latency | {self.average_latency_seconds:.2f}s |",
            f"| Total Tokens | {self.total_tokens} (prompt: {self.total_prompt_tokens}, completion: {self.total_completion_tokens}) |",
            "",
            "### Per-Case Results",
            "",
            "| Case ID | Category | Expected | Predicted | TP | FP | FN | P | R | F1 | Status |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for r in self.case_results:
            status = "✅ PASS" if (r.false_positives == 0 and r.false_negatives == 0) else "❌ FAIL"
            lines.append(
                f"| `{r.case_id}` | {r.category} | {len(r.expected_stale_sections)} | {len(r.predicted_stale_sections)} | {r.true_positives} | {r.false_positives} | {r.false_negatives} | {r.precision:.0%} | {r.recall:.0%} | {r.f1:.0%} | {status} |"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class BenchmarkDiffReport:
    baseline_summary: EvaluationSummary
    candidate_summary: EvaluationSummary
    delta_precision: float
    delta_recall: float
    delta_f1: float
    delta_accuracy: float
    delta_false_positive_rate: float
    delta_latency_seconds: float
    delta_tokens: int
    delta_cost_usd: float
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline_summary.to_dict(),
            "candidate": self.candidate_summary.to_dict(),
            "delta_precision": self.delta_precision,
            "delta_recall": self.delta_recall,
            "delta_f1": self.delta_f1,
            "delta_accuracy": self.delta_accuracy,
            "delta_false_positive_rate": self.delta_false_positive_rate,
            "delta_latency_seconds": self.delta_latency_seconds,
            "delta_tokens": self.delta_tokens,
            "delta_cost_usd": self.delta_cost_usd,
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
        }

    def to_markdown(self) -> str:
        def _delta_badge(delta: float, higher_is_better: bool = True) -> str:
            if abs(delta) < 1e-4:
                return "⚪ Neutral"
            is_good = (delta > 0) if higher_is_better else (delta < 0)
            sign = "+" if delta > 0 else ""
            prefix = "🟢" if is_good else "🔴"
            return f"{prefix} {sign}{delta:.1%}"

        lines = [
            "## Benchmark Regression & Comparison Report",
            "",
            "| Metric | Baseline | Candidate | Delta |",
            "| :--- | :--- | :--- | :--- |",
            f"| **Precision** | {self.baseline_summary.precision:.1%} | {self.candidate_summary.precision:.1%} | {_delta_badge(self.delta_precision, True)} |",
            f"| **Recall** | {self.baseline_summary.recall:.1%} | {self.candidate_summary.recall:.1%} | {_delta_badge(self.delta_recall, True)} |",
            f"| **F1 Score** | {self.baseline_summary.f1:.1%} | {self.candidate_summary.f1:.1%} | {_delta_badge(self.delta_f1, True)} |",
            f"| **Exact Match Accuracy** | {self.baseline_summary.accuracy:.1%} | {self.candidate_summary.accuracy:.1%} | {_delta_badge(self.delta_accuracy, True)} |",
            f"| False Positive Rate | {self.baseline_summary.false_positive_rate:.1%} | {self.candidate_summary.false_positive_rate:.1%} | {_delta_badge(self.delta_false_positive_rate, False)} |",
            f"| Avg Latency | {self.baseline_summary.average_latency_seconds:.2f}s | {self.candidate_summary.average_latency_seconds:.2f}s | {self.delta_latency_seconds:+.2f}s |",
            f"| Total Tokens | {self.baseline_summary.total_tokens} | {self.candidate_summary.total_tokens} | {self.delta_tokens:+d} |",
            f"| Estimated Delta Cost | — | — | ${self.delta_cost_usd:+.4f} |",
            "",
        ]

        if self.regressions:
            lines.append(f"### ⚠️ Regressions ({len(self.regressions)})\n")
            for reg in self.regressions:
                lines.append(f"- 🔴 `{reg}`: Regression detected against baseline.")
            lines.append("")
        else:
            lines.append("### ✅ Regressions (0)\nNo accuracy regressions detected.\n")

        if self.improvements:
            lines.append(f"### 🚀 Improvements ({len(self.improvements)})\n")
            for imp in self.improvements:
                lines.append(f"- 🟢 `{imp}`: Passed in candidate after failing in baseline.")
            lines.append("")
        else:
            lines.append("### Improvements (0)\n")

        return "\n".join(lines)
