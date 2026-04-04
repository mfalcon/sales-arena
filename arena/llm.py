"""LLM client and JSON parser for Sales Arena."""

import json
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


@dataclass
class TokenUsage:
    """Accumulated token usage."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    """OpenAI-compatible LLM client."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "local-model",
        temperature: float = 0.7,
        max_tokens: int = 1500,
        api_key: str = "not-needed",
    ):
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.usage = TokenUsage()

    def send(self, messages: list[dict], json_mode: bool = False) -> str:
        """Send a messages list to the LLM and return the response content.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            json_mode: If True, prefill assistant response with '{' to force JSON.

        Returns:
            Response content as string.
        """
        msgs = list(messages)

        if json_mode:
            msgs.append({"role": "assistant", "content": "{"})

        response = self._client.chat.completions.create(
            model=self.model,
            messages=msgs,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        content = response.choices[0].message.content

        if json_mode:
            content = "{" + content

        if response.usage:
            self.usage.prompt_tokens += response.usage.prompt_tokens
            self.usage.completion_tokens += response.usage.completion_tokens

        return content


# --- JSON Parser ---


def extract_json(text: str) -> Optional[dict]:
    """Extract JSON from LLM response text.

    Handles: plain JSON, markdown code blocks, embedded JSON in text,
    trailing commas, single quotes.
    """
    if not text:
        return None

    # Try direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try markdown code blocks
    code_block_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    code_blocks = re.findall(code_block_pattern, text)
    if code_blocks:
        for block in code_blocks:
            result = _try_parse_json(block)
            if result is not None:
                return result

    # Find outermost JSON objects in text
    candidates = []
    brace_count = 0
    start = -1
    for i, char in enumerate(text):
        if char == "{":
            if brace_count == 0:
                start = i
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0 and start >= 0:
                json_str = text[start : i + 1]
                result = _try_parse_json(json_str)
                if result is not None:
                    candidates.append(result)
                start = -1

    if not candidates:
        return None

    # Skip reasoning-only candidates
    reasoning_keys = {"thought", "analysis", "reasoning", "thinking"}
    for c in candidates:
        if not (c.keys() & reasoning_keys):
            return c

    result = candidates[0]
    for key in reasoning_keys:
        result.pop(key, None)
    return result


def _try_parse_json(text: str) -> Optional[dict]:
    """Try to parse JSON with common fixes."""
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fix trailing commas
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Single quotes to double quotes
    fixed = text.replace("'", '"')
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    return None
