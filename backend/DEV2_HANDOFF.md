# BE-59: Developer 2 backend handoff

This document describes the existing Developer 2 code in the **outer BranchWise
repository** on `backend-dev2`. Both **BE-28 and BE-38 are PARTIAL**. Passing unit
tests means the helpers work as tested; it does not mean either feature is fully
integrated. Neither helper is currently called by the Flask application.

## BE-28: Supplier input validation — PARTIAL

Source: [supplier_validation.py](supplier_validation.py).

`validate_supplier_input(data)` checks a Python dictionary and returns a dictionary
of field errors. An empty result (`{}`) means these checks passed, not that a
supplier was created or saved.

- `name` is the only required field. It must be a string containing something
  other than whitespace.
- `contact_person`, `phone`, `email`, `address`, and `notes` are optional. They may
  be omitted, `None`, or strings, including blank strings. Other types are rejected.
- A nonblank email gets a basic syntax check. This does not verify that the
  address exists. Phone validation checks the type only, not the number format.
- Input is not changed or cleaned. Whitespace is stripped only for checks.
- Unknown fields are ignored. This helper is **not a whitelist of fields safe to
  save**. These internal field labels do not define database columns or an API.

Example, in a Python session started from the outer `backend/` directory:

```python
from supplier_validation import validate_supplier_input

validate_supplier_input({"name": "Main Supplier"})  # {}
validate_supplier_input({"name": " "})
# {"name": "Supplier name is required."}
```

Missing integration: supplier routes and an agreed request/response format,
database design and persistence, authentication and business ownership checks,
duplicate-name checks, field length limits, and deactivation rules. Before wiring
this helper into a save operation, agree these rules and explicitly select the
fields that may be stored. Then add tests for the integrated behavior. No supplier
CRUD workflow (create, read, update, delete) is implemented by this helper.

## BE-38: Internal AI text service — PARTIAL

Sources: [ai_service.py](ai_service.py), [ai_config.py](ai_config.py), and
[ai_client.py](ai_client.py). Phase 3 adds [gemini_provider.py](gemini_provider.py).

`run_ai(request_data, provider=None)` accepts a dictionary containing **only**
`text`, with a nonblank string value. It loads server settings, checks the input
length, and calls a provider supplied by application code. It checks that the
returned dictionary also contains only a nonblank `text` string, fits the output
limit, and does not contain the exact configured API key. Text keeps its original
whitespace; limits count Python characters, including whitespace, not AI tokens.

These are provisional internal Python conventions, not a public API contract or
an external provider's response format. There is no BE-38 Flask endpoint. Network
work belongs to the explicitly supplied adapter; the service has no database,
generated SQL/Python execution, or business-record modification behavior.

Example, from an outer `backend/` Python session, with a nonblank `GEMINI_API_KEY`
or fallback `AI_API_KEY` already set in the process environment:

```python
from ai_service import run_ai

def fake_provider(text, *, api_key, timeout):
    return {"text": "A local example answer."}

result = run_ai({"text": "Hello"}, provider=fake_provider)
# {"ok": True, "data": {"text": "A local example answer."}, "error": None}
```

The fake provider above makes no API calls. The provider argument comes from
trusted application code, never from a field in the request dictionary. With valid
input and settings, leaving it out returns `AI_NOT_CONFIGURED`.

### Phase 3 Gemini adapter

For later authorized live use, import `gemini_provider` from `gemini_provider` and
explicitly pass it as `run_ai(request_data, provider=gemini_provider)`. This path
can make a real API request; tests replace the SDK client with a mock. There is no
automatic provider selection or SDK client creation when importing the adapter.

The adapter uses the pinned official `google-genai` SDK and
`client.interactions.create(model=..., input=text, store=False, timeout=...)`.
The default model is `gemini-3.8-flash`. It returns the documented `output_text`
from completed responses as `{"text": ...}`, which still passes through the
existing service checks. Missing or unfinished responses are rejected with
`AI_INVALID_OUTPUT`, even if they contain partial text.
`store=False` requests stateless interaction behavior. The SDK client is closed
after each call. See [Google's Interactions guide](https://ai.google.dev/gemini-api/docs/interactions-overview).

The Interactions timeout argument uses seconds. The adapter maps SDK timeout and
connection exceptions to the existing `AI_TIMEOUT` and `AI_CONNECTION_ERROR`
flow. Authentication failures and other request errors become the existing safe
`AI_PROVIDER_ERROR`; exception details are not returned or logged by this code.

Supporting functions:

- `load_ai_config()` reads and validates environment settings on each call. It
  returns private settings, including the key, or raises `ValueError` with a safe
  configuration message. Do not log or return the settings dictionary.
- `call_ai_provider(text, config, provider=None)` calls
  `provider(text, api_key=config["api_key"], timeout=config["timeout_seconds"])`.
  It returns `(output, None)` or `(None, error_code)`. It catches provider failures
  but does not validate the output structure; `run_ai` performs that check.
  Use `run_ai` for the complete current validation flow.

A failed `run_ai` call returns
`{"ok": False, "data": None, "error": {"code": ..., "message": ...}}`.
The existing codes are:

| Code | Meaning |
| --- | --- |
| `AI_INVALID_INPUT` | Input shape, text, or length failed validation. |
| `AI_CONFIG_ERROR` | Required settings are missing or invalid. |
| `AI_NOT_CONFIGURED` | No callable provider was supplied. |
| `AI_TIMEOUT` | The supplied provider raised `TimeoutError`. |
| `AI_CONNECTION_ERROR` | The supplied provider raised `OSError`, including `ConnectionError`. |
| `AI_PROVIDER_ERROR` | The supplied provider raised another exception. |
| `AI_INVALID_OUTPUT` | Output shape, text, length, or the exact-key check failed. |

Remaining integration: an authorized team member must verify credentials, model
access, and behavior with a live Gemini API test. The application contract,
authentication, request handling, and Flask wiring still need separate work.
The generic callable boundary does not itself enforce a deadline; the Gemini
adapter passes the timeout to the SDK. Phase 3 has mocked tests only and does not
prove a working live connection. **BE-38 remains PARTIAL.**

## Environment variables and dependencies

BE-28 requires no environment variables. BE-38 reads these names from the process
environment; no secret values are included here:

| Name | Requirement in the current AI code |
| --- | --- |
| `GEMINI_API_KEY` | Preferred key, nonblank after trimming; no default. |
| `AI_API_KEY` | Backward-compatible fallback if the Gemini key is unset or blank. At least one key is required. |
| `GEMINI_MODEL` | Optional; unset or blank uses `gemini-3.8-flash`. Nonblank values are trimmed. |
| `AI_TIMEOUT_SECONDS` | Optional; default `10`. Must be a positive, finite number. |
| `AI_MAX_INPUT_CHARS` | Optional; default `10000`. Must be a positive integer. |
| `AI_MAX_OUTPUT_CHARS` | Optional; default `20000`. Must be a positive integer. |

The AI module does not load `.env` files, even though `python-dotenv` is listed in
[requirements.txt](requirements.txt). Set process variables before calling it, or
add deliberate environment loading during later application integration. The
existing [.env.example](.env.example) lists `FLASK_DEBUG`, `SECRET_KEY`, `DB_HOST`,
`DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME`, but not the AI names above.
Those template names are not read or required by these two helpers. When both
keys are set, `GEMINI_API_KEY` takes precedence for every supplied provider,
including test fakes. Keys are never accepted from the request dictionary.

Supplier validation and the generic service use Python's standard library and
local modules. The Gemini adapter requires `google-genai==2.34.0`; its timeout
and connection exception imports match that pinned SDK's Interactions behavior
and must be checked when upgrading. Tests require `pytest`. The shared requirements
also list Flask, the MySQL connector, and related packages; their presence does
not implement database or application integration.

## Run all existing outer-backend tests

Run these PowerShell commands from the **outer repository root**, not the nested
`Branch-wise/` directory. On a fresh checkout, create an environment and install
the existing requirements first; skip this setup if already prepared:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Run the full outer-backend suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -c backend\pytest.ini backend
```

[pytest.ini](pytest.ini) supplies the backend import path. The available tests are:

- [test_supplier_validation.py](tests/test_supplier_validation.py): field rules,
  multiple errors, unchanged input, and checks against database/network access.
- [test_ai_service.py](tests/test_ai_service.py): configuration, input/output
  rules and limits, fake provider calls, safe errors, key protection, and checks
  against networking and execution of generated text.
- [test_gemini_provider.py](tests/test_gemini_provider.py): mocked Gemini SDK
  calls, configuration precedence, response text, request failures, shared limits,
  and key protection, with networking blocked.

Tests supply fake AI settings themselves. No real credentials, running Flask app,
database, or AI service are needed. They test local behavior with fake providers
and blocked external access, not real integrations. **BE-28 and BE-38 remain
PARTIAL even when all tests pass.**
