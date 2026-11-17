"""BE-38 Phase 3: explicit Google Gemini adapter for the internal text service.

Use run_ai(request_data, provider=gemini_provider) from trusted application code.
Importing this module creates no SDK client and makes no network requests.
"""

import os


def gemini_provider(text, *, api_key, timeout):
    """Return normalized text; let the existing service validate it.

    GEMINI_MODEL defaults to the approved gemini-3.8-flash. The key and timeout
    come from the existing configuration flow, not from the request body.
    """
    from google import genai
    from google.genai.errors import ContinuationInterruptedError

    # Interactions uses these errors in the pinned google-genai 2.34.0 SDK;
    # they differ from the older google.genai.errors hierarchy.
    from google.genai._interactions import (
        APIConnectionError,
        APITimeoutError,
    )

    model = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"

    try:
        with genai.Client(api_key=api_key, enterprise=False) as client:
            response = client.interactions.create(
                model=model,
                input=text,
                store=False,
                timeout=timeout,  # Interactions accepts seconds here.
            )
            if getattr(response, "status", None) != "completed":
                return {"text": None}
            return {"text": getattr(response, "output_text", None)}
    except APITimeoutError:
        raise TimeoutError("AI service timed out.") from None
    except APIConnectionError:
        raise ConnectionError("AI service could not be reached.") from None
    except ContinuationInterruptedError as error:
        # The SDK can wrap a transport failure after receiving partial output.
        # Keep that output and any continuation tokens private.
        cause = error.__cause__
        if isinstance(cause, (APITimeoutError, TimeoutError)):
            raise TimeoutError("AI service timed out.") from None
        if isinstance(cause, (APIConnectionError, OSError)):
            raise ConnectionError("AI service could not be reached.") from None
        raise
