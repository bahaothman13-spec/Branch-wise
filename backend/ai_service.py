"""Provisional internal text service for BE-38 Phase 1.

Input and normalized provider output each contain only a nonempty "text"
string. Length limits count Python characters, including whitespace, not
tokens. These internal choices can change when the contract is approved.

Provider text is data only. This module has no database access, SQL execution,
generated-code execution, or business-record modification behavior.
"""

from ai_client import call_ai_provider
from ai_config import load_ai_config


ERROR_MESSAGES = {
    "AI_INVALID_INPUT": "Provide a nonempty text string within the input limit.",
    "AI_CONFIG_ERROR": "AI service configuration is missing or invalid.",
    "AI_NOT_CONFIGURED": "AI provider is not configured.",
    "AI_TIMEOUT": "AI service timed out.",
    "AI_CONNECTION_ERROR": "AI service could not be reached.",
    "AI_PROVIDER_ERROR": "AI service is unavailable.",
    "AI_INVALID_OUTPUT": "AI service returned an invalid response.",
}


def _failure(code):
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": ERROR_MESSAGES[code]},
    }


def _has_text(data):
    return (
        isinstance(data, dict)
        and set(data) == {"text"}
        and isinstance(data["text"], str)
        and bool(data["text"].strip())
    )


def run_ai(request_data, provider=None):
    """Validate a request and return {ok, data, error} with safe messages.

    The provider is an application argument, never a field in request_data.
    No provider is selected automatically, and credentials come only from
    server environment settings. The returned result is suitable for later
    application integration but does not define a public Flask endpoint.
    """
    if not _has_text(request_data):
        return _failure("AI_INVALID_INPUT")

    try:
        config = load_ai_config()
    except ValueError:
        return _failure("AI_CONFIG_ERROR")

    text = request_data["text"]
    if len(text) > config["max_input_chars"]:
        return _failure("AI_INVALID_INPUT")

    output, error = call_ai_provider(text, config, provider)
    if error is not None:
        return _failure(error)

    if not _has_text(output):
        return _failure("AI_INVALID_OUTPUT")

    response_text = output["text"]
    if (
        len(response_text) > config["max_output_chars"]
        or config["api_key"] in response_text
    ):
        return _failure("AI_INVALID_OUTPUT")

    return {"ok": True, "data": {"text": response_text}, "error": None}
