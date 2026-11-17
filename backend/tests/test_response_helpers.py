"""Tests for shared response helpers and safe API error handling."""

from app import create_app
from response_helpers import error_response, success_response


def test_success_response_preserves_payload_and_returns_json():
    app = create_app()

    with app.test_request_context():
        response, status_code = success_response({"status": "ok"})

    assert status_code == 200
    assert response.is_json
    assert response.get_json() == {"status": "ok"}


def test_success_response_supports_created_status():
    app = create_app()

    with app.test_request_context():
        response, status_code = success_response(
            {"data": {"id": 12}}, status_code=201
        )

    assert status_code == 201
    assert response.get_json() == {"data": {"id": 12}}


def test_error_response_preserves_validation_error_shape():
    app = create_app()

    with app.test_request_context():
        response, status_code = error_response(
            errors={"email": "Enter a valid email address."}, status_code=400
        )

    assert status_code == 400
    assert response.get_json() == {
        "errors": {"email": "Enter a valid email address."}
    }


def test_error_response_returns_simple_error_message():
    app = create_app()

    with app.test_request_context():
        response, status_code = error_response(
            "Permission denied.", status_code=403
        )

    assert status_code == 403
    assert response.get_json() == {"error": "Permission denied."}


def test_unknown_api_route_returns_json_404():
    app = create_app()
    response = app.test_client().get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Resource not found."}


def test_http_error_handler_uses_safe_messages_for_auth_and_conflict_statuses():
    from flask import abort

    app = create_app()

    @app.get("/api/test-http-error/<int:status_code>")
    def fail_with_http_status(status_code):
        abort(status_code, description="private DB_PASSWORD detail")

    client = app.test_client()
    expected_errors = {
        401: "Authentication is required.",
        403: "Permission denied.",
        409: "The request conflicts with the current resource state.",
        422: "Request validation failed.",
        501: "This operation is not implemented.",
    }

    for status_code, message in expected_errors.items():
        response = client.get(f"/api/test-http-error/{status_code}")

        assert response.status_code == status_code
        assert response.get_json() == {"error": message}
        assert "private DB_PASSWORD detail" not in response.get_data(as_text=True)


def test_unexpected_exception_does_not_leak_sensitive_details():
    app = create_app()
    app.config["TESTING"] = True

    @app.get("/api/test-unexpected-error")
    def fail_for_test():
        raise RuntimeError("private_password DB_PASSWORD stack trace")

    response = app.test_client().get("/api/test-unexpected-error")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert response.get_json() == {
        "error": "An unexpected server error occurred."
    }
    assert "private_password" not in body
    assert "DB_PASSWORD" not in body
    assert "stack trace" not in body
