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
        msgs = _sanitize_messages(list(messages))

        # Determine JSON mode strategy based on API
        use_response_format = json_mode and self._supports_response_format()
        use_prefill = json_mode and not use_response_format

        if use_prefill:
            msgs.append({"role": "assistant", "content": "{"})

        # Retry up to 2 times on empty responses, bump temperature on retries
        content = ""
        for attempt in range(3):
            temp = min(self.temperature + (attempt * 0.3), 1.5)
            # Use max_completion_tokens for newer OpenAI models, max_tokens for others
            token_param = (
                {"max_completion_tokens": self.max_tokens}
                if "gpt-5" in self.model or "o1" in self.model or "o3" in self.model
                else {"max_tokens": self.max_tokens}
            )
            extra = {}
            if use_response_format:
                extra["response_format"] = {"type": "json_object"}
            response = self._client.chat.completions.create(
                model=self.model,
                messages=msgs,
                temperature=temp,
                **token_param,
                **extra,
            )

            content = response.choices[0].message.content or ""

            # Fallback: read reasoning_content if content is empty
            if not content.strip():
                msg = response.choices[0].message
                reasoning = getattr(msg, "reasoning_content", None) or ""
                if reasoning.strip():
                    content = reasoning

            if response.usage:
                self.usage.prompt_tokens += response.usage.prompt_tokens
                self.usage.completion_tokens += response.usage.completion_tokens

            if content.strip():
                break

        # Strip reasoning/analysis tags from output
        content = _strip_reasoning_tags(content)

        if use_prefill:
            content = "{" + content

        return content

    def _supports_response_format(self) -> bool:
        """Check if the model supports response_format parameter (OpenAI API)."""
        return "api.openai.com" in self.base_url or "gpt" in self.model


def _strip_reasoning_tags(text: str) -> str:
    """Remove reasoning/analysis tags that reasoning models leak into output."""
    # Remove <analysis>...</analysis> blocks
    text = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL)
    # Remove <thinking>...</thinking> blocks
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    # Remove unclosed <analysis> tags (model didn't close them)
    text = re.sub(r"<analysis>.*", "", text, flags=re.DOTALL)
    # Extract content after "## Final Answer" or "## Response" (reasoning models)
    final_match = re.search(r"##\s*(?:Final Answer|Response)\s*\n", text, re.IGNORECASE)
    if final_match:
        text = text[final_match.end():]
    else:
        # Remove leading markdown headers with reasoning keywords
        text = re.sub(r"^[\s\-#]*(?:Reasoning|Analysis|Thinking|Chain of Thought).*?\n", "", text, flags=re.IGNORECASE)
        # Remove everything before "---" separator if it looks like reasoning above
        parts = re.split(r"\n---+\n", text)
        if len(parts) > 1 and len(parts[-1].strip()) > 20:
            text = parts[-1]
    return text.strip()


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """Ensure messages follow the user/assistant alternation pattern.

    Some model templates (Qwen, Gemma) are strict about:
    - First non-system message must be "user"
    - Messages must alternate user/assistant
    - Last message before generation must be "user"

    This function merges consecutive same-role messages and ensures
    the conversation starts with "user" and ends with "user".
    """
    if not messages:
        return messages

    # Separate system messages from the rest, skip empty content
    system_msgs = [m for m in messages if m["role"] == "system"]
    chat_msgs = [
        m for m in messages
        if m["role"] != "system" and m.get("content", "").strip()
    ]

    if not chat_msgs:
        return messages

    # Merge consecutive same-role messages
    merged = []
    for msg in chat_msgs:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(dict(msg))

    # Ensure first message is "user"
    if merged[0]["role"] != "user":
        if system_msgs:
            system_msgs[-1]["content"] += "\n\n" + merged[0]["content"]
            merged = merged[1:]
        else:
            merged[0]["role"] = "user"

    # Ensure last message is "user"
    if merged and merged[-1]["role"] != "user":
        merged.append({"role": "user", "content": "Continue the conversation."})

    return system_msgs + merged


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
