from __future__ import annotations

import ast
import os
from typing import Any, Protocol, Sequence

from openai import OpenAI

from codoc.models import CodeChunk, DocSection

_MAX_CHUNK_BODY_CHARS = 1200
_MAX_SECTION_CHARS = 2000


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    """Thin OpenAI-compatible embeddings adapter."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
        batch_size: int = 128,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.batch_size = max(1, batch_size)
        self._client = client
        self._api_key = api_key
        self._base_url = base_url

    def _get_client(self) -> OpenAI:
        if self._client is None:
            resolved_key = (
                self._api_key
                if self._api_key is not None
                else os.environ.get("OPENAI_API_KEY")
            )
            self._client = OpenAI(api_key=resolved_key, base_url=self._base_url)
        return self._client

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        vectors: list[list[float]] = []
        items = list(texts)
        for start in range(0, len(items), self.batch_size):
            batch = items[start : start + self.batch_size]
            response = client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
        return vectors



class FastEmbedProvider:
    """Local, private, CPU-friendly embeddings adapter using FastEmbed (ONNX)."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        batch_size: int = 128,
        model: Any = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = max(1, batch_size)
        self._model = model

    def _get_model(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            # ponytail: all-MiniLM-L6-v2 on ONNX CPU; upgrade to GPU/multi-model when larger local models needed
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        embeddings = model.embed(list(texts), batch_size=self.batch_size)
        return [list(float(x) for x in emb) for emb in embeddings]


def get_default_embedding_provider(
    provider_name: str = "fastembed",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> EmbeddingProvider:
    if provider_name == "openai" or (provider_name != "fastembed" and (api_key or base_url)):
        return OpenAIEmbeddingProvider(
            api_key=api_key,
            model=model or "text-embedding-3-small",
            base_url=base_url,
        )
    return FastEmbedProvider(
        model_name=model or "sentence-transformers/all-MiniLM-L6-v2",
    )

def _normalized_body_summary(chunk: CodeChunk) -> str:
    try:
        module = ast.parse(chunk.raw_source)
        if module.body and isinstance(
            module.body[0], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            stmt_list = list(module.body[0].body)
            if (
                stmt_list
                and isinstance(stmt_list[0], ast.Expr)
                and isinstance(stmt_list[0].value, ast.Constant)
                and isinstance(stmt_list[0].value.value, str)
            ):
                stmt_list = stmt_list[1:]
            body_text = " ".join(ast.unparse(stmt) for stmt in stmt_list)
            return body_text[:_MAX_CHUNK_BODY_CHARS]
    except SyntaxError:
        pass
    normalized = " ".join(chunk.raw_source.split())
    return normalized[:_MAX_CHUNK_BODY_CHARS]


def format_chunk_for_embedding(chunk: CodeChunk) -> str:
    parts = [chunk.qualified_name, chunk.signature]
    if chunk.docstring:
        parts.append(chunk.docstring.strip())
    body_summary = _normalized_body_summary(chunk)
    if body_summary:
        parts.append(body_summary)
    return "\n".join(parts)


def format_section_for_embedding(section: DocSection) -> str:
    lines = section.raw_content.strip().splitlines()
    # Skip redundant leading Markdown heading line since heading_path encodes full hierarchy
    if lines and lines[0].lstrip().startswith("#"):
        body = "\n".join(lines[1:]).strip()
    else:
        body = "\n".join(lines).strip()
    if len(body) > _MAX_SECTION_CHARS:
        body = body[:_MAX_SECTION_CHARS]
    return f"{section.heading_path}\n{body}" if body else section.heading_path
