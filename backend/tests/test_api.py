"""API tests for routes that are waiting for database integration."""

import pytest

from app import create_app


@pytest.fixture
def client():
    """Create a Flask test client without starting a server or database."""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health_endpoint_returns_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_registration_with_valid_input_returns_501(client):
    response = client.post(
        "/api/auth/register",
        json={
            "owner_name": "Test Owner",
            "business_name": "Test Business",
            "email": "owner@example.com",
            "password": "valid-password",
        },
    )

    assert response.status_code == 501


@pytest.mark.parametrize(
    "registration_data",
    [
        {"owner_name": "Test Owner"},
        {
            "owner_name": "Test Owner",
            "business_name": "Test Business",
            "email": "not-an-email",
            "password": "valid-password",
        },
        {
            "owner_name": "Test Owner",
            "business_name": "Test Business",
            "email": "owner@example.com",
            "password": "short",
        },
    ],
)
def test_registration_with_invalid_input_returns_400(client, registration_data):
    response = client.post("/api/auth/register", json=registration_data)

    assert response.status_code == 400


def test_login_with_valid_input_returns_501(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "valid-password"},
    )

    assert response.status_code == 501


@pytest.mark.parametrize(
    "login_data",
    [
        {"email": "owner@example.com"},
        {"email": "bad-email", "password": "valid-password"},
    ],
)
def test_login_with_invalid_input_returns_400(client, login_data):
    response = client.post("/api/auth/login", json=login_data)

    assert response.status_code == 400


def test_logout_returns_501_until_sessions_are_implemented(client):
    response = client.post("/api/auth/logout")

    assert response.status_code == 501


def test_branch_creation_with_valid_input_returns_501(client):
    response = client.post(
        "/api/branches",
        json={
            "name": "Main Branch",
            "address": "12 Main Street",
            "phone": "+962 6 555 0100",
            "notes": "Near the city center",
        },
    )

    assert response.status_code == 501
    assert "authentication" in response.get_json()["message"]


def test_branch_creation_without_optional_fields_returns_501(client):
    response = client.post("/api/branches", json={"name": "Main Branch"})

    assert response.status_code == 501


def test_branch_list_returns_501_until_authentication_and_database_are_ready(client):
    response = client.get("/api/branches")

    assert response.status_code == 501
    assert "authentication" in response.get_json()["message"]


def test_branch_details_with_positive_id_returns_501(client):
    response = client.get("/api/branches/1")

    assert response.status_code == 501


def test_branch_deactivation_with_positive_id_returns_501(client):
    response = client.patch("/api/branches/1/deactivate")

    assert response.status_code == 501
    assert "authentication" in response.get_json()["message"]


@pytest.mark.parametrize("branch_id", ["0", "abc", "-1"])
def test_branch_deactivation_with_invalid_id_returns_400(client, branch_id):
    response = client.patch(f"/api/branches/{branch_id}/deactivate")

    assert response.status_code == 400
    assert response.get_json()["errors"]["branch_id"] == "Must be a positive integer."


@pytest.mark.parametrize("branch_id", ["0", "abc", "-1"])
def test_branch_details_with_invalid_id_returns_400(client, branch_id):
    response = client.get(f"/api/branches/{branch_id}")

    assert response.status_code == 400
    assert response.get_json()["errors"]["branch_id"] == "Must be a positive integer."


def test_branch_update_with_valid_data_returns_501(client):
    response = client.patch(
        "/api/branches/1",
        json={"name": "Updated Branch", "address": "New address", "notes": "Updated"},
    )

    assert response.status_code == 501
    assert "authentication" in response.get_json()["message"]


@pytest.mark.parametrize(
    "branch_id,branch_data",
    [
        ("0", {"name": "Updated Branch"}),
        ("abc", {"name": "Updated Branch"}),
        ("1", {}),
        ("1", {"name": "   "}),
        ("1", {"name": 123}),
        ("1", {"phone": None}),
        ("1", {"notes": ["note"]}),
        ("1", {"business_id": 10}),
    ],
)
def test_branch_update_with_invalid_data_returns_400(client, branch_id, branch_data):
    response = client.patch(f"/api/branches/{branch_id}", json=branch_data)

    assert response.status_code == 400
    assert "errors" in response.get_json()


@pytest.mark.parametrize(
    "branch_data",
    [
        {},
        {"name": "   "},
        {"name": 123},
        {"name": "Main Branch", "address": 12},
        {"name": "Main Branch", "phone": None},
        {"name": "Main Branch", "notes": ["note"]},
    ],
)
def test_branch_creation_with_invalid_input_returns_400(client, branch_data):
    response = client.post("/api/branches", json=branch_data)

    assert response.status_code == 400
    assert "errors" in response.get_json()


def test_user_creation_with_valid_input_returns_501(client):
    response = client.post(
        "/api/users",
        json={
            "name": "Test Employee",
            "email": "employee@example.com",
            "role": "Employee",
            "branch_id": 1,
            "initial_password": "valid-password",
        },
    )

    assert response.status_code == 501


@pytest.mark.parametrize(
    "user_data",
    [
        {
            "name": "Test Employee",
            "email": "employee@example.com",
            "role": "Employee",
            "branch_id": 1,
            "initial_password": "short",
        },
        {
            "name": "Test Employee",
            "email": "employee@example.com",
            "role": "Owner",
            "branch_id": 1,
            "initial_password": "valid-password",
        },
        {
            "name": "Test Employee",
            "email": "employee@example.com",
            "role": "Employee",
            "branch_id": 0,
            "initial_password": "valid-password",
        },
    ],
)
def test_user_creation_with_invalid_input_returns_400(client, user_data):
    response = client.post("/api/users", json=user_data)

    assert response.status_code == 400


def test_user_status_change_with_valid_input_returns_501(client):
    response = client.patch("/api/users/1/status", json={"is_active": False})

    assert response.status_code == 501


@pytest.mark.parametrize(
    ("user_id", "status_data"),
    [
        ("0", {"is_active": True}),
        ("1", {"is_active": "false"}),
        ("1", {}),
    ],
)
def test_user_status_change_with_invalid_input_returns_400(client, user_id, status_data):
    response = client.patch(f"/api/users/{user_id}/status", json=status_data)

    assert response.status_code == 400


def test_password_change_with_valid_input_returns_501(client):
    response = client.patch(
        "/api/users/me/password",
        json={
            "current_password": "current-password",
            "new_password": "new-password",
            "confirm_password": "new-password",
        },
    )

    assert response.status_code == 501


@pytest.mark.parametrize(
    "password_data",
    [
        {
            "current_password": "current-password",
            "new_password": "short",
            "confirm_password": "short",
        },
        {
            "current_password": "current-password",
            "new_password": "new-password",
            "confirm_password": "different-password",
        },
        {"current_password": "current-password", "new_password": "new-password"},
    ],
)
def test_password_change_with_invalid_input_returns_400(client, password_data):
    response = client.patch("/api/users/me/password", json=password_data)

    assert response.status_code == 400
