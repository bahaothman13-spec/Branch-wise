"""Communication boundary for an application-supplied AI provider.

The callable may be a test fake or the explicit Gemini adapter. Adapters handle
network timeouts and translate provider responses into the internal format.
"""


def call_ai_provider(text, config, provider=None):
    """Return (normalized output, None) or (None, safe error code).

    The application supplies the callable; a request must never select it.
    Its provisional Python signature is provider(text, *, api_key, timeout),
    returning {"text": "..."}. This is not an external provider API contract.

    Passing a timeout setting does not enforce a wall-clock deadline here.
    Never log exception details: they may contain credentials or private data.
    """
    if not callable(provider):
        return None, "AI_NOT_CONFIGURED"

    try:
        output = provider(
            text,
            api_key=config["api_key"],
            timeout=config["timeout_seconds"],
        )
    except TimeoutError:
        return None, "AI_TIMEOUT"
    except OSError:
        # ConnectionError and standard socket errors are OSError subclasses.
        return None, "AI_CONNECTION_ERROR"
    except Exception:
        # Keep unexpected failures from a supplied adapter private as well.
        return None, "AI_PROVIDER_ERROR"

    return output, None
