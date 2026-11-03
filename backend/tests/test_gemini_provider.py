"""Gemini adapter tests with fake credentials and a fully mocked SDK client."""

import json
import socket
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from google import genai
from google.genai._interactions import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from google.genai.errors import ContinuationInterruptedError
from google.genai.interactions import Interaction

from ai_config import load_ai_config
from ai_service import run_ai
from gemini_provider import gemini_provider


FAKE_GEMINI_KEY = "fake-gemini-key-for-local-tests"
FAKE_AI_KEY = "fake-generic-ai-key-for-local-tests"
DEFAULT_MODEL = "gemini-3.8-flash"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, caplog):
    """Ignore developer credentials and model settings in every test."""
    caplog.set_level("DEBUG")
    for name in (
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "AI_API_KEY",
        "AI_TIMEOUT_SECONDS",
        "AI_MAX_INPUT_CHARS",
        "AI_MAX_OUTPUT_CHARS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI_KEY)


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Detect accidental HTTP, socket, or database access, even if swallowed."""
    attempted_access = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(httpx.Client, "send", attempted_access)
    monkeypatch.setattr(socket, "create_connection", attempted_access)
    monkeypatch.setattr(socket, "getaddrinfo", attempted_access)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted_access)
    monkeypatch.setattr(sqlite3, "connect", attempted_access)

    yield attempted_access

    attempted_access.assert_not_called()


@pytest.fixture(autouse=True)
def sdk_client(monkeypatch):
    """Replace the SDK constructor so no test can create a live client."""
    client = Mock(name="gemini_client")
    client.interactions.create.return_value = Interaction.model_validate(
        {
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": "A local Gemini answer."}],
                }
            ],
        }
    )
    context = MagicMock(name="gemini_client_context")
    context.__enter__.return_value = client
    context.__exit__.return_value = False
    factory = Mock(name="gemini_client_factory", return_value=context)
    monkeypatch.setattr(genai, "Client", factory)
    return SimpleNamespace(
        factory=factory,
        context=context,
        client=client,
        create=client.interactions.create,
    )


def assert_no_private_details(result, caplog, *private_details):
    private_values = (FAKE_GEMINI_KEY, FAKE_AI_KEY, *private_details)
    for private_value in private_values:
        assert private_value not in json.dumps(result)
        assert private_value not in caplog.text
        for record in caplog.records:
            assert private_value not in repr(record.__dict__)


def assert_safe_failure(result, expected_code, caplog, *private_details):
    assert set(result) == {"ok", "data", "error"}
    assert result["ok"] is False
    assert result["data"] is None
    assert set(result["error"]) == {"code", "message"}
    assert result["error"]["code"] == expected_code
    assert isinstance(result["error"]["message"], str)
    assert result["error"]["message"].strip()
    assert_no_private_details(result, caplog, *private_details)


def test_explicit_gemini_call_uses_nonstored_interaction_and_seconds_timeout(
    monkeypatch, sdk_client, caplog
):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "2.5")

    result = run_ai({"text": "  Hello  "}, provider=gemini_provider)

    assert result == {
        "ok": True,
        "data": {"text": "A local Gemini answer."},
        "error": None,
    }
    sdk_client.factory.assert_called_once_with(
        api_key=FAKE_GEMINI_KEY, enterprise=False
    )
    sdk_client.create.assert_called_once_with(
        model=DEFAULT_MODEL, input="  Hello  ", store=False, timeout=2.5
    )
    sdk_client.context.__enter__.assert_called_once_with()
    sdk_client.context.__exit__.assert_called_once_with(None, None, None)
    assert_no_private_details(result, caplog)


@pytest.mark.parametrize(
    "configured_model,expected_model",
    [
        (None, DEFAULT_MODEL),
        ("", DEFAULT_MODEL),
        (" \t ", DEFAULT_MODEL),
        ("  local-test-model  ", "local-test-model"),
    ],
)
def test_model_default_and_environment_override(
    monkeypatch, sdk_client, configured_model, expected_model
):
    if configured_model is not None:
        monkeypatch.setenv("GEMINI_MODEL", configured_model)

    assert run_ai({"text": "Hello"}, provider=gemini_provider)["ok"] is True
    assert sdk_client.create.call_args.kwargs["model"] == expected_model


@pytest.mark.parametrize(
    "gemini_key,ai_key,expected_key",
    [
        (FAKE_GEMINI_KEY, None, FAKE_GEMINI_KEY),
        (FAKE_GEMINI_KEY, FAKE_AI_KEY, FAKE_GEMINI_KEY),
        (None, FAKE_AI_KEY, FAKE_AI_KEY),
        ("", FAKE_AI_KEY, FAKE_AI_KEY),
        (" \t ", FAKE_AI_KEY, FAKE_AI_KEY),
        (f"  {FAKE_GEMINI_KEY}  ", FAKE_AI_KEY, FAKE_GEMINI_KEY),
        (None, f"  {FAKE_AI_KEY}  ", FAKE_AI_KEY),
    ],
)
def test_key_precedence_fallback_and_trimming(
    monkeypatch, sdk_client, gemini_key, ai_key, expected_key
):
    for name, value in (("GEMINI_API_KEY", gemini_key), ("AI_API_KEY", ai_key)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    assert load_ai_config() == {
        "api_key": expected_key,
        "timeout_seconds": 10.0,
        "max_input_chars": 10000,
        "max_output_chars": 20000,
    }
    assert run_ai({"text": "Hello"}, provider=gemini_provider)["ok"] is True
    sdk_client.factory.assert_called_once_with(api_key=expected_key, enterprise=False)


@pytest.mark.parametrize("gemini_key,ai_key", [(None, None), ("", ""), (" \t ", " ")])
def test_missing_keys_fail_before_creating_client(
    monkeypatch, sdk_client, caplog, gemini_key, ai_key
):
    for name, value in (("GEMINI_API_KEY", gemini_key), ("AI_API_KEY", ai_key)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        load_ai_config()
    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_CONFIG_ERROR", caplog)
    sdk_client.factory.assert_not_called()


def test_gemini_settings_do_not_select_a_provider_automatically(
    monkeypatch, sdk_client, caplog
):
    monkeypatch.setenv("GEMINI_MODEL", "local-test-model")

    result = run_ai({"text": "Hello"})

    assert_safe_failure(result, "AI_NOT_CONFIGURED", caplog)
    sdk_client.factory.assert_not_called()


@pytest.mark.parametrize("field", ["api_key", "provider", "model"])
def test_request_cannot_select_credentials_provider_or_model(
    sdk_client, caplog, field
):
    result = run_ai({"text": "Hello", field: "untrusted"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_INVALID_INPUT", caplog)
    sdk_client.factory.assert_not_called()


@pytest.mark.parametrize(
    "status_code,error_type",
    [
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (429, RateLimitError),
        (500, InternalServerError),
    ],
)
def test_sdk_status_failures_return_safe_provider_error(
    sdk_client, caplog, status_code, error_type
):
    private_details = f"Private provider diagnostic: {FAKE_GEMINI_KEY}"
    request = httpx.Request("POST", "https://example.invalid/interactions")
    response = httpx.Response(status_code, request=request)
    sdk_client.create.side_effect = error_type(
        private_details, response=response, body={"private": FAKE_GEMINI_KEY}
    )

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_PROVIDER_ERROR", caplog, private_details)
    sdk_client.context.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "failure_kind,expected_code",
    [("timeout", "AI_TIMEOUT"), ("connection", "AI_CONNECTION_ERROR")],
)
def test_sdk_transport_failures_are_translated_safely(
    sdk_client, caplog, failure_kind, expected_code
):
    private_details = f"Private transport diagnostic: {FAKE_GEMINI_KEY}"
    request = httpx.Request(
        "POST",
        "https://example.invalid/interactions",
        headers={"x-goog-api-key": FAKE_GEMINI_KEY},
    )
    if failure_kind == "timeout":
        error = APITimeoutError(request=request)
    else:
        error = APIConnectionError(message=private_details, request=request)
    error.__cause__ = RuntimeError(private_details)
    sdk_client.create.side_effect = error

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, expected_code, caplog, private_details)
    sdk_client.context.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "cause_kind,expected_code",
    [
        ("sdk-timeout", "AI_TIMEOUT"),
        ("builtin-timeout", "AI_TIMEOUT"),
        ("sdk-connection", "AI_CONNECTION_ERROR"),
        ("os-error", "AI_CONNECTION_ERROR"),
        ("unknown", "AI_PROVIDER_ERROR"),
        ("missing", "AI_PROVIDER_ERROR"),
    ],
)
def test_interrupted_continuation_hides_partial_output_and_tokens(
    sdk_client, caplog, cause_kind, expected_code
):
    private_details = f"Private continuation diagnostic: {FAKE_GEMINI_KEY}"
    private_output = "Private partial output from an unfinished interaction"
    private_token = "private-continuation-token"
    private_interaction_id = "private-interaction-id"
    private_hop_id = "private-continuation-hop-id"
    request = httpx.Request("POST", "https://example.invalid/interactions")
    if cause_kind == "sdk-timeout":
        cause = APITimeoutError(request=request)
    elif cause_kind == "builtin-timeout":
        cause = TimeoutError(private_details)
    elif cause_kind == "sdk-connection":
        cause = APIConnectionError(message=private_details, request=request)
    elif cause_kind == "os-error":
        cause = OSError(private_details)
    elif cause_kind == "unknown":
        cause = RuntimeError(private_details)
    else:
        cause = None
    interrupted = ContinuationInterruptedError(
        private_details,
        partial_interaction=SimpleNamespace(
            status="requires_continuation", output_text=private_output
        ),
        continuation_token=private_token,
        interaction_id=private_interaction_id,
        continuation_ids=[private_hop_id],
    )
    interrupted.__cause__ = cause
    sdk_client.create.side_effect = interrupted

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(
        result,
        expected_code,
        caplog,
        private_details,
        private_output,
        private_token,
        private_interaction_id,
        private_hop_id,
    )
    sdk_client.context.__exit__.assert_called_once()


def test_chained_unexpected_sdk_failure_does_not_expose_private_details(
    sdk_client, caplog
):
    private_details = f"Private chained diagnostic: {FAKE_GEMINI_KEY}"

    def fail(**kwargs):
        try:
            raise ValueError(private_details)
        except ValueError as cause:
            raise RuntimeError("Adapter failed") from cause

    sdk_client.create.side_effect = fail

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_PROVIDER_ERROR", caplog, private_details)


@pytest.mark.parametrize(
    "response",
    [
        None,
        SimpleNamespace(status="completed"),
        {"status": "completed", "output_text": "Not an SDK response object"},
        SimpleNamespace(status="completed", output_text=None),
        SimpleNamespace(status="completed", output_text=""),
        SimpleNamespace(status="completed", output_text=" \n\t "),
        SimpleNamespace(status="completed", output_text=123),
        SimpleNamespace(status="completed", output_text=False),
        SimpleNamespace(status="completed", output_text=["Unexpected text list"]),
    ],
    ids=["none", "missing-text", "dict", "null-text", "empty", "blank", "number", "boolean", "list"],
)
def test_empty_or_nontext_sdk_output_is_rejected(sdk_client, caplog, response):
    sdk_client.create.return_value = response

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_INVALID_OUTPUT", caplog)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(output_text="Unfinished answer"),
        SimpleNamespace(status=None, output_text="Unfinished answer"),
        SimpleNamespace(status="", output_text="Unfinished answer"),
        SimpleNamespace(status="in_progress", output_text="Unfinished answer"),
        SimpleNamespace(status="failed", output_text="Unfinished answer"),
        SimpleNamespace(status="requires_action", output_text="Unfinished answer"),
        SimpleNamespace(status="requires_continuation", output_text="Unfinished answer"),
    ],
    ids=["missing", "null", "blank", "in-progress", "failed", "requires-action", "continuation"],
)
def test_noncompleted_interaction_is_rejected_even_with_text(
    sdk_client, caplog, response
):
    sdk_client.create.return_value = response

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_INVALID_OUTPUT", caplog, "Unfinished answer")


@pytest.mark.parametrize("text,accepted", [(" hi  ", True), ("hello!", False)])
def test_input_character_limit_applies_before_sdk_call(
    monkeypatch, sdk_client, caplog, text, accepted
):
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "5")

    result = run_ai({"text": text}, provider=gemini_provider)

    if accepted:
        assert result["ok"] is True
        assert sdk_client.create.call_args.kwargs["input"] == text
    else:
        assert_safe_failure(result, "AI_INVALID_INPUT", caplog)
        sdk_client.factory.assert_not_called()


@pytest.mark.parametrize("text,accepted", [(" hi  ", True), ("hello!", False)])
def test_output_character_limit_applies_after_sdk_call(
    monkeypatch, sdk_client, caplog, text, accepted
):
    monkeypatch.setenv("AI_MAX_OUTPUT_CHARS", "5")
    sdk_client.create.return_value = SimpleNamespace(status="completed", output_text=text)

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    if accepted:
        assert result == {"ok": True, "data": {"text": text}, "error": None}
    else:
        assert_safe_failure(result, "AI_INVALID_OUTPUT", caplog)


def test_active_api_key_in_sdk_output_is_rejected(sdk_client, caplog):
    sdk_client.create.return_value = SimpleNamespace(
        status="completed", output_text=f"Private value: {FAKE_GEMINI_KEY}"
    )

    result = run_ai({"text": "Hello"}, provider=gemini_provider)

    assert_safe_failure(result, "AI_INVALID_OUTPUT", caplog)
