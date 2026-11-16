"""BE-43 Phase 1: prepare a question and previously authorized business facts.

This is an internal, provisional helper, NOT a Flask API, Gemini prompt, or
proof that the facts were authorized. Another trusted service must check user,
business, and branch permissions and prepare the facts before calling it.

The result contains data only; it does not contact Gemini, query a database,
execute instructions, or answer the question. An empty facts dictionary means
no facts were supplied, not that business figures are zero.
"""

import json
import math
from decimal import Decimal


MAX_QUESTION_CHARS = 2000  # Provisional local safety limits
MAX_FACTS_JSON_CHARS = 8000
MAX_NESTING = 20


def _to_json_value(value, depth=0):
    """Copy supported business data to JSON-safe values without changing input."""
    if depth > MAX_NESTING:
        raise ValueError("Business facts are too deeply nested.")

    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("Business fact field names must be strings.")
            result[key] = _to_json_value(item, depth + 1)
        return result

    if type(value) in (list, tuple):
        return [_to_json_value(item, depth + 1) for item in value]

    if value is None or type(value) in (str, bool, int):
        return value

    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Business facts must contain finite numbers.")
        return value

    if type(value) is Decimal:
        if not value.is_finite():
            raise ValueError("Business facts must contain finite numbers.")
        return str(value)  # Preserve monetary precision instead of using float

    raise ValueError("Business facts contain an unsupported value type.")


def prepare_business_question(question, authorized_facts):
    """Return question text and JSON-serialized, caller-authorized facts.

    Provisional contract:
      - question: nonblank string, preserved exactly as submitted.
      - authorized_facts: dictionary supplied by a trusted future data layer.
        Nested JSON-friendly values and finite Decimal numbers are accepted;
        Decimal is serialized as a precise JSON string.
      - result: {"question", "facts_json", "facts_supplied"}.

    ``facts_supplied`` only means the supplied dictionary is nonempty. It does
    NOT establish data completeness, accuracy, permission, or answerability.
    This function never treats fact values as trusted instructions.
    """
    if type(question) is not str or not question.strip():
        raise ValueError("Question must be a nonblank string.")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError("Question is too long.")
    if type(authorized_facts) is not dict:
        raise ValueError("Authorized facts must be a dictionary.")

    try:
        safe_facts = _to_json_value(authorized_facts)
        facts_json = json.dumps(safe_facts, ensure_ascii=False,
                                allow_nan=False, separators=(",", ":"))
    except (RecursionError, OverflowError, ValueError, TypeError):
        raise ValueError("Business facts contain invalid or unsupported data.") from None

    if len(facts_json) > MAX_FACTS_JSON_CHARS:
        raise ValueError("Business facts are too large.")

    return {
        "question": question,
        "facts_json": facts_json,
        "facts_supplied": bool(safe_facts),
    }
