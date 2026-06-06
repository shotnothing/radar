from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class PredictionReview:
    makes_sense: bool
    should_show: bool
    confidence: float
    reason: str
    relevant_history_indices: list[int]
    raw: dict[str, Any]


@dataclass(frozen=True)
class SuggestedAction:
    title: str
    body: str
    action_text: str
    raw: dict[str, Any]


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Any | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get(OPENAI_API_KEY_ENV)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def review_prediction(
        self,
        prediction: Any,
        history: list[Any],
        *,
        context: Any | None = None,
    ) -> PredictionReview:
        payload = {
            "prediction": prediction,
            "history": _last_history(history),
            "context": context,
        }
        parsed = self._call_json(_REVIEW_SYSTEM_PROMPT, payload)
        return PredictionReview(
            makes_sense=_as_bool(parsed.get("makes_sense")),
            should_show=_as_bool(parsed.get("should_show"), default=_as_bool(parsed.get("makes_sense"))),
            confidence=_clamp_float(parsed.get("confidence"), 0.0, 1.0),
            reason=str(parsed.get("reason") or ""),
            relevant_history_indices=_as_int_list(parsed.get("relevant_history_indices")),
            raw=parsed,
        )

    def suggest_action(
        self,
        prediction: Any,
        history: list[Any],
        *,
        context: Any | None = None,
    ) -> SuggestedAction:
        payload = {
            "prediction": prediction,
            "history": _last_history(history),
            "context": context,
        }
        parsed = self._call_json(_SUGGEST_ACTION_SYSTEM_PROMPT, payload)
        return SuggestedAction(
            title=str(parsed.get("title") or "Suggested action"),
            body=str(parsed.get("body") or ""),
            action_text=str(parsed.get("action_text") or parsed.get("primary_action") or "Open"),
            raw=parsed,
        )

    def _call_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError(f"{OPENAI_API_KEY_ENV} is required to call the LLM")

        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=True, sort_keys=True, default=str),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(request_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(
                f"LLM request failed with HTTP {exc.code}: {_safe_error_body(body)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError("LLM request timed out") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM response did not contain chat content: {body}") from exc
        return parse_json_object(content)


def review_prediction(
    prediction: Any,
    history: list[Any],
    *,
    context: Any | None = None,
    client: LLMClient | None = None,
) -> PredictionReview:
    return (client or LLMClient()).review_prediction(prediction, history, context=context)


def suggest_action(
    prediction: Any,
    history: list[Any],
    *,
    context: Any | None = None,
    client: LLMClient | None = None,
) -> SuggestedAction:
    return (client or LLMClient()).suggest_action(prediction, history, context=context)


def parse_json_object(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise LLMError(f"LLM returned JSON that is not an object: {parsed!r}")
    return parsed


def _last_history(history: list[Any]) -> list[Any]:
    return list(history or [])[-4:]


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    numbers = []
    for item in value:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return numbers


def _safe_error_body(body: str) -> str:
    text = str(body or "").strip()
    try:
        payload = json.loads(text)
        message = ((payload.get("error") or {}).get("message") or "").strip()
        if message:
            text = message
    except (json.JSONDecodeError, AttributeError):
        pass
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-REDACTED", text)
    return text[:500]


_REVIEW_SYSTEM_PROMPT = """You decide whether a Radar prediction is useful in the user's current context.
Use only the supplied prediction, history, and context.
History contains the last 4 normalized events/messages at most.

Return only a JSON object with:
- makes_sense: boolean, whether the prediction follows from the recent history.
- should_show: boolean, whether the app should show this suggestion now.
- confidence: number from 0 to 1 for your judgment.
- reason: short string, one sentence max.
- relevant_history_indices: array of zero-based indices from the supplied history that matter.
"""


_SUGGEST_ACTION_SYSTEM_PROMPT = """You write compact UI text for a Radar suggested action.
The suggestion must be actionable and based only on the supplied prediction, history, and context.
Do not mention model confidence or implementation details.

Return only a JSON object with:
- title: short user-facing title.
- body: one short sentence explaining what will happen.
- action_text: short button label with the action to take.
"""
