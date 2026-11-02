"""Mocked tests for the provisional AI service; no API or database is needed."""

import json
import os
import sqlite3
from unittest.mock import Mock

import pytest

from ai_client import call_ai_provider
from ai_config import load_ai_config
from ai_service import run_ai


FAKE_API_KEY = "private-test-key-for-ai-service"


@pytest.fixture(autouse=True)
def isolated_ai_settings(monkeypatch, caplog):
    """Use fake settings instead of a developer's environment or .env file."""
    caplog.set_level("DEBUG")
    for name in (
        "AI_API_KEY",
        "AI_TIMEOUT_SECONDS",
        "AI_MAX_INPUT_CHARS",
        "AI_MAX_OUTPUT_CHARS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_API_KEY", FAKE_API_KEY)


@pytest.fixture
def provider():
    return Mock(return_value={"text": "A safe answer."})


def assert_safe_error(result, expected_code):
    """Check the common error structure without depending on message wording."""
    assert set(result) == {"ok", "data", "error"}
    assert result["ok"] is False
    assert result["data"] is None
    assert set(result["error"]) == {"code", "message"}
    assert result["error"]["code"] == expected_code
    assert isinstance(result["error"]["message"], str)
    assert result["error"]["message"].strip()
    assert FAKE_API_KEY not in json.dumps(result)


def test_configuration_uses_provisional_defaults():
    assert load_ai_config() == {
        "api_key": FAKE_API_KEY,
        "timeout_seconds": 10.0,
        "max_input_chars": 10000,
        "max_output_chars": 20000,
    }


def test_configuration_accepts_environment_overrides(monkeypatch):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "25")
    monkeypatch.setenv("AI_MAX_OUTPUT_CHARS", "50")

    settings = load_ai_config()

    assert settings["timeout_seconds"] == 2.5
    assert settings["max_input_chars"] == 25
    assert settings["max_output_chars"] == 50


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_api_key_is_handled_safely(monkeypatch, provider, key):
    if key is None:
        monkeypatch.delenv("AI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("AI_API_KEY", key)

    with pytest.raises(ValueError):
        load_ai_config()

    assert_safe_error(run_ai({"text": "Hello"}, provider), "AI_CONFIG_ERROR")
    provider.assert_not_called()


@pytest.mark.parametrize(
    "name,value",
    [
        ("AI_TIMEOUT_SECONDS", "0"),
        ("AI_TIMEOUT_SECONDS", "-1"),
        ("AI_TIMEOUT_SECONDS", "nan"),
        ("AI_TIMEOUT_SECONDS", "inf"),
        ("AI_TIMEOUT_SECONDS", "-inf"),
        ("AI_TIMEOUT_SECONDS", "invalid"),
        ("AI_TIMEOUT_SECONDS", ""),
        ("AI_MAX_INPUT_CHARS", "0"),
        ("AI_MAX_INPUT_CHARS", "-1"),
        ("AI_MAX_INPUT_CHARS", "1.5"),
        ("AI_MAX_INPUT_CHARS", "invalid"),
        ("AI_MAX_OUTPUT_CHARS", "0"),
        ("AI_MAX_OUTPUT_CHARS", "-1"),
        ("AI_MAX_OUTPUT_CHARS", "1.5"),
        ("AI_MAX_OUTPUT_CHARS", ""),
    ],
)
def test_invalid_configuration_stops_the_provider(
    monkeypatch, provider, name, value
):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        load_ai_config()

    assert_safe_error(run_ai({"text": "Hello"}, provider), "AI_CONFIG_ERROR")
    provider.assert_not_called()


@pytest.mark.parametrize(
    "request_data",
    [
        None,
        [],
        "Hello",
        123,
        {},
        {"text": None},
        {"text": 123},
        {"text": False},
        {"text": ""},
        {"text": " \n\t "},
        {"text": "Hello", "api_key": "client-supplied-key"},
        {"text": "Hello", "provider": "client-supplied-provider"},
    ],
)
def test_invalid_input_is_rejected_without_calling_provider(provider, request_data):
    assert_safe_error(run_ai(request_data, provider), "AI_INVALID_INPUT")
    provider.assert_not_called()


def test_success_preserves_text_and_supplies_private_settings(
    monkeypatch, provider, caplog
):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "2.5")

    result = run_ai({"text": "  Hello  "}, provider)

    assert result == {
        "ok": True,
        "data": {"text": "A safe answer."},
        "error": None,
    }
    provider.assert_called_once_with(
        "  Hello  ", api_key=FAKE_API_KEY, timeout=2.5
    )
    assert FAKE_API_KEY not in json.dumps(result)
    assert FAKE_API_KEY not in caplog.text


def test_input_at_character_limit_is_accepted(monkeypatch, provider):
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "5")

    assert run_ai({"text": " hi  "}, provider)["ok"] is True
    provider.assert_called_once_with(
        " hi  ", api_key=FAKE_API_KEY, timeout=10.0
    )


def test_input_above_character_limit_is_rejected(monkeypatch, provider):
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "5")

    assert_safe_error(run_ai({"text": "hello!"}, provider), "AI_INVALID_INPUT")
    provider.assert_not_called()


@pytest.mark.parametrize("missing_provider", [None, "unconfigured", {}])
def test_missing_provider_returns_controlled_error(missing_provider):
    assert_safe_error(
        run_ai({"text": "Hello"}, missing_provider), "AI_NOT_CONFIGURED"
    )


@pytest.mark.parametrize(
    "exception_type,expected_code",
    [
        (TimeoutError, "AI_TIMEOUT"),
        (ConnectionError, "AI_CONNECTION_ERROR"),
        (OSError, "AI_CONNECTION_ERROR"),
        (RuntimeError, "AI_PROVIDER_ERROR"),
    ],
)
def test_provider_failures_do_not_expose_private_details(
    caplog, exception_type, expected_code
):
    private_details = f"Private provider diagnostic: {FAKE_API_KEY}"
    failing_provider = Mock(side_effect=exception_type(private_details))

    result = run_ai({"text": "Hello"}, failing_provider)

    assert_safe_error(result, expected_code)
    assert private_details not in json.dumps(result)
    assert FAKE_API_KEY not in caplog.text
    assert private_details not in caplog.text


@pytest.mark.parametrize(
    "output",
    [
        None,
        [],
        "A plain string is not the normalized contract",
        {},
        {"text": None},
        {"text": 123},
        {"text": False},
        {"text": ""},
        {"text": " \n\t "},
        {"text": "An answer", "extra": "Unexpected provider field"},
    ],
)
def test_malformed_provider_output_is_rejected(output):
    fake_provider = Mock(return_value=output)

    assert_safe_error(run_ai({"text": "Hello"}, fake_provider), "AI_INVALID_OUTPUT")


def test_output_at_character_limit_is_accepted(monkeypatch):
    monkeypatch.setenv("AI_MAX_OUTPUT_CHARS", "5")
    fake_provider = Mock(return_value={"text": "hello"})

    assert run_ai({"text": "Hello"}, fake_provider)["data"] == {"text": "hello"}


def test_output_above_character_limit_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_MAX_OUTPUT_CHARS", "5")
    fake_provider = Mock(return_value={"text": "hello!"})

    assert_safe_error(run_ai({"text": "Hello"}, fake_provider), "AI_INVALID_OUTPUT")


@pytest.mark.parametrize(
    "output",
    [
        {"text": f"The private key is {FAKE_API_KEY}"},
        {"text": "An answer", "api_key": FAKE_API_KEY},
        {"error": FAKE_API_KEY},
    ],
)
def test_credentials_in_provider_output_are_not_returned_or_logged(output, caplog):
    fake_provider = Mock(return_value=output)

    assert_safe_error(run_ai({"text": "Hello"}, fake_provider), "AI_INVALID_OUTPUT")
    assert FAKE_API_KEY not in caplog.text


def test_invalid_configuration_does_not_echo_its_value(monkeypatch, caplog):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", FAKE_API_KEY)

    with pytest.raises(ValueError) as error:
        load_ai_config()

    assert FAKE_API_KEY not in str(error.value)
    assert_safe_error(run_ai({"text": "Hello"}), "AI_CONFIG_ERROR")
    assert FAKE_API_KEY not in caplog.text


def test_client_calls_the_fake_provider_without_a_real_api():
    fake_provider = Mock(return_value={"text": "A normalized answer"})

    output, error = call_ai_provider("Hello", load_ai_config(), fake_provider)

    assert output == {"text": "A normalized answer"}
    assert error is None
    fake_provider.assert_called_once_with(
        "Hello", api_key=FAKE_API_KEY, timeout=10.0
    )


@pytest.mark.parametrize(
    "untrusted_text",
    [
        "SELECT * FROM users;",
        "DROP TABLE suppliers;",
        "UPDATE inventory SET quantity = 0;",
        "__import__('os').system('echo generated-code')",
    ],
)
def test_sql_and_python_like_text_is_returned_as_data(monkeypatch, untrusted_text):
    database_connection = Mock(side_effect=AssertionError("Database access attempted"))
    command_execution = Mock(side_effect=AssertionError("Command execution attempted"))
    monkeypatch.setattr(sqlite3, "connect", database_connection)
    monkeypatch.setattr(os, "system", command_execution)
    fake_provider = Mock(return_value={"text": untrusted_text})

    result = run_ai({"text": untrusted_text}, fake_provider)

    assert result == {"ok": True, "data": {"text": untrusted_text}, "error": None}
    database_connection.assert_not_called()
    command_execution.assert_not_called()
