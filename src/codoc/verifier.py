from __future__ import annotations

import json
import os
import re
from typing import Any
import urllib.request

from openai import OpenAI

from codoc.models import ChangeComplexity, StalenessVerdict, SuspectSection


def build_verification_prompt(suspect: SuspectSection) -> str:
    sec = suspect.section
    deltas_block: list[str] = []

    for delta in suspect.deltas:
        old_src = (
            delta.old_chunk.raw_source
            if delta.old_chunk is not None
            else "(newly added symbol)"
        )
        new_src = (
            delta.new_chunk.raw_source
            if delta.new_chunk is not None
            else "(deleted symbol)"
        )
        deltas_block.append(
            f"### Symbol: {delta.chunk_id} (Type: {delta.delta_type})\n"
            f"#### Old Code:\n```python\n{old_src.strip()}\n```\n"
            f"#### New Code:\n```python\n{new_src.strip()}\n```"
        )

    code_changes_text = "\n\n".join(deltas_block)

    return (
        "You are an expert documentation verifier. Verify whether the provided documentation section "
        "is stale (inaccurate, misleading, or outdated) due to the code changes.\n\n"
        f"## Documentation Section\n"
        f"- File: {sec.file_path}\n"
        f"- Heading Path: {sec.heading_path}\n"
        f"- Section ID: {sec.section_id}\n\n"
        f"### Current Documentation Content (Head Revision):\n"
        f"```markdown\n{sec.raw_content.strip()}\n```\n\n"
        f"## Code Changes Linked to this Section\n"
        f"{code_changes_text}\n\n"
        "## Output Instructions\n"
        "Return ONLY a valid JSON object with the following schema:\n"
        "{\n"
        '  "is_stale": boolean,\n'
        '  "diagnosis": string (specific description of discrepancy, or reason documentation is accurate),\n'
        '  "change_complexity": "simple" | "complex",\n'
        '  "confidence": number between 0.0 and 1.0\n'
        "}"
    )


def _extract_json_block(raw_text: str) -> str:
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        return match.group(1).strip()
    return text


def parse_staleness_verdict_json(
    raw_text: str,
    section_id: str | None = None,
    suspect_section: SuspectSection | None = None,
) -> StalenessVerdict:
    target_section_id = (
        section_id
        if section_id is not None
        else (suspect_section.section.section_id if suspect_section is not None else "")
    )

    cleaned = _extract_json_block(raw_text)
    data = json.loads(cleaned)

    if not isinstance(data, dict):
        raise ValueError("LLM response did not contain a JSON object.")

    if not isinstance(data.get("is_stale"), bool):
        raise ValueError("Field 'is_stale' must be a boolean.")

    diagnosis = data.get("diagnosis")
    if not isinstance(diagnosis, str) or not diagnosis.strip():
        raise ValueError("Field 'diagnosis' must be a non-empty string.")

    complexity = data.get("change_complexity")
    if complexity not in ("simple", "complex"):
        raise ValueError("Field 'change_complexity' must be 'simple' or 'complex'.")

    conf_val = data.get("confidence")
    if not isinstance(conf_val, (int, float)) or not (0.0 <= conf_val <= 1.0):
        raise ValueError("Field 'confidence' must be a float between 0.0 and 1.0.")

    return StalenessVerdict(
        section_id=target_section_id,
        is_stale=data["is_stale"],
        diagnosis=diagnosis.strip(),
        change_complexity=complexity,
        confidence=float(conf_val),
        suspect_section=suspect_section,
    )


class LLMStalenessVerifier:
    """Provider-agnostic LLM staleness verifier backing OpenAI, Anthropic, and compatible endpoints."""

    def __init__(
        self,
        provider: str = "openai",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any = None,
        max_retries: int = 2,
    ) -> None:
        self.provider = provider.lower()
        self._api_key = api_key
        self._base_url = base_url
        self.max_retries = max(0, max_retries)
        self._client = client

        if model is not None:
            self.model = model
        elif self.provider == "anthropic":
            self.model = "claude-3-5-sonnet-20241022"
        else:
            self.model = "gpt-4o"

    def _get_openai_client(self) -> OpenAI:
        if self._client is None:
            resolved_key = (
                self._api_key
                if self._api_key is not None
                else os.environ.get("OPENAI_API_KEY")
            )
            self._client = OpenAI(api_key=resolved_key, base_url=self._base_url)
        return self._client  # type: ignore[no-any-return]

    def _call_openai(self, prompt: str, system_prompt: str | None = None) -> str:
        client = self._get_openai_client()
        completions: Any = client.chat.completions
        sys_msg = system_prompt or "You are a code documentation assistant. Always respond in valid JSON."
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception:
            # ponytail: fallback if custom provider rejects response_format; upgrade when strict JSON schema is parameterized
            resp = completions.create(
                model=self.model,
                messages=messages,
            )
        return str(resp.choices[0].message.content or "")

    def _call_anthropic(self, prompt: str, system_prompt: str | None = None) -> str:
        messages = [{"role": "user", "content": prompt}]
        sys_kwargs: dict[str, Any] = {"system": system_prompt} if system_prompt else {}
        if self._client is not None:
            if hasattr(self._client, "messages"):
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    messages=messages,
                    **sys_kwargs,
                )
                first_block = resp.content[0]
                if hasattr(first_block, "text"):
                    return str(first_block.text)
                return str(first_block)
            if callable(self._client):
                return str(self._client(prompt))

        resolved_key = (
            self._api_key
            if self._api_key is not None
            else os.environ.get("ANTHROPIC_API_KEY")
        )
        base = self._base_url or "https://api.anthropic.com/v1"
        url = base if base.endswith("/messages") else f"{base.rstrip('/')}/messages"
        payload = json.dumps({
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "x-api-key": resolved_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return str(res_data["content"][0]["text"])

    def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        if self.provider == "anthropic":
            return self._call_anthropic(prompt, system_prompt=system_prompt)
        return self._call_openai(prompt, system_prompt=system_prompt)

    def verify_staleness(self, suspect: SuspectSection) -> StalenessVerdict:
        prompt = build_verification_prompt(suspect)
        attempts = 1 + self.max_retries
        last_error = ""

        for _ in range(attempts):
            try:
                if self.provider == "anthropic":
                    raw_text = self._call_anthropic(prompt)
                else:
                    raw_text = self._call_openai(prompt)

                return parse_staleness_verdict_json(
                    raw_text=raw_text,
                    suspect_section=suspect,
                )
            except Exception as exc:
                last_error = str(exc)
                continue

        # Graceful degradation after retries exhausted
        return StalenessVerdict(
            section_id=suspect.section.section_id,
            is_stale=False,
            diagnosis=f"Verification failed due to malformed LLM response: {last_error}",
            change_complexity="simple",
            confidence=0.0,
            suspect_section=suspect,
        )
