"""Chat model access. MetaLlama talks to the Meta Model API (its OpenAI-compatible endpoint)."""

import json
import re
import time
from typing import Protocol

import httpx

LLAMA_BASE_URL = "https://api.meta.ai/v1"
_MAX_RETRIES = 4
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """Ask for a reply that is a JSON object matching `schema`, and parse it."""
        ...


def parse_json_object(text: str) -> dict:
    """Models sometimes wrap JSON in a code fence or surround it with prose; take the object."""
    text = _FENCE.sub("", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"Model reply is not JSON: {text[:200]!r}") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"Model reply is not JSON: {text[:200]!r}") from e
    if not isinstance(data, dict):
        raise LLMError("Model reply is not a JSON object")
    return data


class MetaLlama:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = LLAMA_BASE_URL,
        http: httpx.Client | None = None,
        reasoning_effort: str | None = None,
    ):
        """`reasoning_effort` ("minimal" ... "max") trades answer quality for speed; None uses the model default."""
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._http = http or httpx.Client(
            base_url=base_url, timeout=90, headers={"Authorization": f"Bearer {api_key}"}
        )

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}},
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        resp = self._post("/chat/completions", body)
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected Meta Model API response: {str(resp)[:200]}") from e
        return parse_json_object(content)

    def _post(self, path: str, body: dict) -> dict:
        resp: httpx.Response | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._http.post(path, json=body)
            except httpx.HTTPError as e:
                raise LLMError(f"Meta Model API request failed: {e}") from e
            if resp.status_code not in (429, 503):
                break
            time.sleep(0.5 * 2**attempt)
        if resp is None:
            raise LLMError("Meta Model API request produced no response")
        if resp.is_error:
            raise LLMError(f"Meta Model API returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()
