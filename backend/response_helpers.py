"""Small JSON response helpers for the BranchWise API."""

from flask import jsonify


def json_response(payload, status_code=200):
    """Return a JSON payload and status without changing its existing shape."""
    return jsonify(payload), status_code


def success_response(payload, status_code=200):
    """Return a JSON success response while preserving the supplied payload."""
    return json_response(payload, status_code)


def error_response(message=None, status_code=400, *, errors=None):
    """Return a safe JSON error, retaining the API's existing validation shape."""
    if errors is not None:
        payload = {"errors": errors}
    else:
        payload = {"error": message or "The request could not be completed."}

    return json_response(payload, status_code)
