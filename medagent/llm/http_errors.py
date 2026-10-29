"""Bounded HTTP diagnostics; provider error bodies may echo prompts or credentials."""

from __future__ import annotations

import re
from typing import Any

import httpx

_MAX_BODY_BYTES = 65536
_SAFE_LABELS = {
    "invalid_request_error", "invalid_request", "invalid_parameter",
    "missing_required_parameter", "unsupported_parameter", "context_length_exceeded",
    "model_not_found", "authentication_error", "permission_error", "not_found_error",
    "rate_limit_error", "rate_limit_exceeded", "insufficient_quota", "api_error",
    "server_error",
}
_SAFE_PARAM = re.compile(
    r"(?:model|temperature|max_tokens|tool_choice|messages|tools|response_format"
    r"|response_format\.type"
    r"|messages\[\d{1,4}\]\.(?:role|content|name|tool_call_id|reasoning_content)"
    r"|messages\[\d{1,4}\]\.tool_calls\[\d{1,4}\]\.(?:id|type|function\.(?:name|arguments))"
    r"|tools\[\d{1,4}\]\.function\.(?:name|description|parameters))",
    re.ASCII,
)


def _message_class(message: Any) -> str:
    # Classify a bounded prefix, never retain free text (including quoted input).
    if not isinstance(message, str):
        return "unclassified"
    text = message[:4096].casefold()
    if "reasoning_content" in text and any(word in text for word in ("missing", "required")):
        return "missing_reasoning_content"
    if "tool_call_id" in text and any(
        word in text for word in ("must be followed", "missing", "not found", "does not match")
    ):
        return "tool_call_response_mismatch"
    if "response_format" in text and any(
        word in text for word in ("unsupported", "not supported")
    ):
        return "unsupported_response_format"
    if any(word in text for word in ("maximum context length", "context length exceeded")):
        return "context_length_exceeded"
    if "missing required parameter" in text:
        return "missing_required_parameter"
    if "unsupported parameter" in text:
        return "unsupported_parameter"
    if "arguments" in text and any(
        word in text for word in ("invalid json", "not valid json", "json parse")
    ):
        return "invalid_tool_arguments"
    return "unclassified"


def safe_http_diagnostics(response: httpx.Response, api_key: str) -> dict[str, Any]:
    """Return allowlisted metadata, not a general-purpose patient-text redactor.

    Unknown labels, arbitrary params, headers/URLs and raw messages are omitted.
    Message classes describe provider wording, not independently proven causes.
    The original response stays on HTTPStatusError for httpx/retry compatibility;
    it must not be dumped into logs.
    """
    details: dict[str, Any] = {
        "status_code": response.status_code,
        "error_type": "HTTPStatusError",
        "message_class": "unclassified",
        "message": "Provider rejected request; raw error message omitted for privacy.",
        "body_format": "non_json",
    }
    if len(response.content) > _MAX_BODY_BYTES:
        details["body_format"] = "omitted_oversized"
        return details
    try:
        body = response.json()
    except (ValueError, UnicodeError, RecursionError):
        return details
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        details["body_format"] = "json_without_error_object"
        return details
    details["body_format"] = "json_error_object"
    for field in ("type", "code"):
        value = error.get(field)
        if isinstance(value, str) and value in _SAFE_LABELS and api_key not in value:
            details[f"provider_{field}"] = value
    param = error.get("param")
    if isinstance(param, str) and _SAFE_PARAM.fullmatch(param) and api_key not in param:
        details["param"] = param
    details["message_class"] = _message_class(error.get("message"))
    return details
