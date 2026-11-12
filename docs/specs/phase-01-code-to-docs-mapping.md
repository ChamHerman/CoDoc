# Phase 1: Code-to-Docs Mapping

**Steps covered:**
- **Step 1.1:** Parse codebase into semantic `CodeChunk` units
- **Step 1.2:** Parse Markdown documentation into `DocSection` units with `CodeReference` extraction
- **Step 1.3:** Build `LinkGraph` connecting `DocSection` and `CodeChunk` nodes via heuristic + embedding edges
- **Step 1.4:** Persist `LinkGraph` JSON index and file-based ChromaDB vector store with content-hash caching

---

## Problem Statement

Engineering teams cannot detect when code changes invalidate technical documentation because there is no machine-readable map linking specific code symbols to the documentation sections that describe them. Without an accurate, persistent index connecting source units to Markdown sections, every pull request requires manual searching across all docs to guess what might have drifted.

## Solution

Build an automated indexing pipeline in Python 3.11+ that parses a repository's Python source code into semantic `CodeChunk` records and its Markdown files into hierarchical `DocSection` records, connects them into a `LinkGraph` using both qualified symbol heuristics and top-K embedding similarity, and persists the graph alongside an incremental, content-hashed ChromaDB vector store designed for fast CI caching with zero generated files committed to git.

## User Stories

1. As a repository maintainer, I want CoDoc to parse Python modules into semantic `CodeChunk` units, so that functions, classes, endpoints, schemas, and CLI commands can be tracked individually rather than as raw files.
2. As a repository maintainer, I want each `CodeChunk` to have a deterministic `chunk_id` derived from module path and qualified symbol name, so that links remain stable across formatting or unrelated line shifts.
3. As a repository maintainer, I want the parser to extract function and async function signatures along with their docstrings and line spans, so that downstream diffing knows exact symbol boundaries and public contracts.
4. As a repository maintainer, I want the parser to extract class definitions including base classes and public methods, so that class-level documentation stays linked to class APIs.
5. As a repository maintainer, I want the parser to recognize decorated API endpoint definitions (such as FastAPI/Starlette route decorators), so that HTTP paths, methods, and handler signatures are captured as semantic units.
6. As a repository maintainer, I want the parser to extract configuration and data schemas (such as Pydantic `BaseModel` classes, dataclasses, and typed settings), so that field additions, renames, or default changes map to configuration docs.
7. As a repository maintainer, I want the parser to extract CLI command definitions (such as Click, Typer, or argparse entrypoints), so that flag and subcommand docs stay linked to CLI code.
8. As a repository maintainer, I want each `CodeChunk` to carry a content hash of its source, so that unchanged chunks do not trigger redundant re-embedding API calls.
9. As a repository maintainer, I want CoDoc to ignore test directories, virtual environments, build artifacts, and user-configured ignore globs during code parsing, so that noise never enters the index.
10. As a documentation author, I want CoDoc to split Markdown files into `DocSection` records by heading hierarchy, so that staleness checks pinpoint the exact subsection rather than flagging an entire document.
11. As a documentation author, I want each `DocSection` to preserve its full `heading_path` (e.g., `Configuration > Environment Variables`) and line span, so that later repairs and PR comments cite exact locations.
12. As a documentation author, I want the Markdown parser to extract `CodeReference` mentions (backticked identifiers, qualified dotted paths, CLI commands, and config keys) from each `DocSection`, so that explicit code references drive high-precision links.
13. As a documentation author, I want code fenced blocks inside a `DocSection` to stay attached to their owning section without splitting on `#` comments inside Python code examples, so that example code never creates fake headings.
14. As a repository maintainer, I want CoDoc to build `heuristic` `LinkEdge` entries when a `DocSection` explicitly mentions a qualified or backticked symbol matching a `CodeChunk`, so that direct references are always captured without relying solely on embeddings.
15. As a repository maintainer, I want heuristic matching to avoid linking bare ambiguous names (like `get`, `run`, `init`, or `config`) across unrelated modules without qualification or context, so that the `LinkGraph` avoids false-positive edges.
16. As a repository maintainer, I want CoDoc to compute embeddings for both `CodeChunk` and `DocSection` texts via a provider-agnostic embedding adapter (defaulting to OpenAI `text-embedding-3-small`), so that implicit semantic relationships are discovered even when symbol names are paraphrased.
17. As a repository maintainer, I want embedding-based linking to pair each `DocSection` with at most top-K (`k=5` default) `CodeChunk` candidates exceeding a configurable cosine similarity threshold (`0.50` default), so that large repositories do not explode with noisy low-signal edges.
18. As a repository maintainer, I want edges discovered by both heuristics and embeddings to merge into `hybrid` `LinkEdge` records carrying combined score and provenance evidence, so that downstream phases can inspect why a link exists.
19. As a repository maintainer, I want the resulting `LinkGraph` persisted as a deterministic JSON artifact and the vectors persisted in a local file-based ChromaDB store outside git-tracked source files, so that CI caches can reuse them without polluting pull requests with binary diffs.
20. As a repository maintainer, I want repeated indexing runs on a repository with only a few modified files to re-embed only chunks and sections whose content hashes changed, so that CI latency and embedding costs stay minimal.
21. As a developer running tests or local indexing, I want a single CLI/pipeline indexing entrypoint that accepts a repository root and an injectable embedding provider, so that full mapping runs cleanly offline in tests or online in CI.

## Implementation Decisions

- **Language and Runtime**: Implement in Python 3.11+ using the standard library `ast` module for Python source parsing and a Markdown AST/token parser that ignores `#` lines inside fenced code blocks.
- **Parser Boundary**: Isolate Python source extraction behind a clean language-parser interface returning `CodeChunk` records so other languages can plug in later without altering `LinkGraph` or downstream phases.
- **Step 1.1 — Semantic `CodeChunk` Extractor**:
  - Extract module-level functions, async functions, classes, methods, decorated API endpoints, Pydantic/dataclass/config schemas, and CLI commands, plus module-level exported constants/settings when configured or typed.
  - Assign deterministic `chunk_id` (`module_path::qualified_name`), `chunk_type`, `signature`, `docstring`, `decorators`, `start_line`, `end_line`, `raw_source`, and `content_hash`.
  - Respect configurable exclusion globs (defaulting to tests, caches, virtualenvs, and hidden build directories).
- **Step 1.2 — Markdown `DocSection` Parser**:
  - Split Markdown documents by ATX headings (`#` through `######`) into nested `DocSection` records with `section_id`, `doc_path`, `heading_path`, `start_line`, `end_line`, `raw_content`, `content_hash`, and extracted `CodeReference` list.
  - Extract `CodeReference` items from inline code spans, qualified dotted identifiers, URL/route paths, and CLI invocation patterns while excluding common non-symbol stopwords.
- **Step 1.3 — `LinkGraph` Builder**:
  - Construct `heuristic` `LinkEdge` items using backticked/qualified identifier matching and module/heading proximity scoring, suppressing unqualified ambiguous common names.
  - Construct `embedding` `LinkEdge` items through a thin embedding provider interface (`embed_texts(list[str]) -> list[list[float]]`), defaulting to OpenAI `text-embedding-3-small` and supporting custom base URLs or separate API keys.
  - Apply configurable cosine similarity threshold (`0.50` default) and per-section top-K cap (`k=5` default); merge overlapping edges into `hybrid` edges with provenance evidence.
- **Step 1.4 — Persistence & Incremental Cache**:
  - Persist `LinkGraph` as versioned JSON and embeddings in a file-based ChromaDB `PersistentClient` directory placed in a cacheable workspace/state directory (never committed to repo git history).
  - Key incremental vector updates by `content_hash`: reuse existing ChromaDB embeddings for unchanged `CodeChunk`/`DocSection` IDs, upsert changed/new records, and prune deleted IDs.
  - Expose one top-level indexing service entrypoint (`build_index(repo_root, embedding_provider, config) -> LinkGraph`).

## Testing Decisions

- **What makes a good test**: Assert only external observable behavior of the indexing seam—given fixture Python modules, Markdown docs, and a deterministic fake embedding provider, assert the emitted `CodeChunk` metadata, `DocSection` boundaries, `LinkGraph` edges/scores, and incremental cache reuse behavior. Never assert private AST helper internals or prompt strings.
- **Modules tested**: End-to-end tests through the top-level indexing entrypoint covering Step 1.1–1.4 together, supplemented by targeted edge-case checks for Python `ast` extraction (endpoints, schemas, CLI decorators), Markdown fenced-code heading immunity, ambiguous name suppression, and incremental hash reuse (verifying unchanged items do not invoke `embed_texts` a second time).
- **Prior art**: Repository currently has no existing test suite; establish clean `pytest` fixture-repo conventions that subsequent phases will reuse.

## Out of Scope

- Non-Python code parsers (TypeScript, Go, Rust, etc.) in v1.
- Generating documentation for unlinked new code or modifying any Markdown files.
- Git diff parsing, LLM staleness verification, or GitHub Action integration (covered in Phases 2–4).
- Committing ChromaDB binaries or generated index artifacts into version control.

## Further Notes

- Canonical vocabulary follows `CONTEXT.md` (`CodeChunk`, `DocSection`, `CodeReference`, `LinkEdge`, `LinkGraph`).
- Keep embedding text construction concise (signature + docstring + normalized body summary for code; heading path + section content for docs) so long files stay within embedding token limits.
