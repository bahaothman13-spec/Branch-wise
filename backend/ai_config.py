"""Environment settings for the provisional BE-38 internal AI service.

The defaults are Phase 1 choices, not limits imposed by an AI provider.
This module does not read .env files or load settings during import.
"""

import math
import os


def load_ai_config():
    """Return private server settings, or raise a safe configuration error.

    The returned dictionary contains credentials. Keep it inside server code;
    never include it in responses, logs, or provider input text.
    """
    message = "AI service configuration is missing or invalid."
    api_key = os.getenv("AI_API_KEY", "").strip()

    try:
        timeout = float(os.getenv("AI_TIMEOUT_SECONDS", "10"))
        max_input = int(os.getenv("AI_MAX_INPUT_CHARS", "10000"))
        max_output = int(os.getenv("AI_MAX_OUTPUT_CHARS", "20000"))
    except ValueError:
        raise ValueError(message) from None

    if (
        not api_key
        or not math.isfinite(timeout)
        or timeout <= 0
        or max_input <= 0
        or max_output <= 0
    ):
        raise ValueError(message)

    return {
        "api_key": api_key,
        "timeout_seconds": timeout,
        "max_input_chars": max_input,
        "max_output_chars": max_output,
    }
