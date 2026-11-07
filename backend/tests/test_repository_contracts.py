"""Focused tests for repository DTOs and explicit Flask dependency injection."""

import pytest

from app import create_app
from permissions import Role
from repository_contracts import (
    BRANCH_REPOSITORY_EXTENSION,
    USER_REPOSITORY_EXTENSION,
    BranchCreateData,
    BranchUpdateData,
    UserCreateData,
    UserRecord,
    install_branch_repository,
    install_user_repository,
)


class FakeBranchRepository:
    """Test-only port implementation; routes must not treat it as production."""

    def list_branches(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def get_branch(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def create_branch_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def update_branch_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def deactivate_branch_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")


class FakeUserRepository:
    """Test-only port implementation with no database or stored state."""

    def list_users(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def get_own_account(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def create_user_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def set_user_active_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def assign_user_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")

    def change_own_password_atomic(self, **_kwargs):
        raise AssertionError("The unimplemented route must not call the fake.")


def make_app():
    return create_app({"APP_ENV": "test", "TESTING": True})


def test_branch_contract_data_is_scoped_by_server_and_update_is_immutable():
    create = BranchCreateData(name="  Downtown  ", phone="555-0100")
    update = BranchUpdateData({"name": "Downtown Center", "notes": None})

    assert create.name == "  Downtown  "
    assert update.changes == {"name": "Downtown Center", "notes": None}
    with pytest.raises(TypeError):
        update.changes["name"] = "Changed"
    assert not hasattr(create, "business_id")
    assert not hasattr(create, "created_by")


@pytest.mark.parametrize(
    "changes",
    [{}, {"business_id": 8}, {"name": None}, {"phone": 4}],
)
def test_branch_update_contract_rejects_invalid_or_untrusted_fields(changes):
    with pytest.raises((TypeError, ValueError)):
        BranchUpdateData(changes)


def test_user_create_contract_limits_roles_and_hides_password_hash():
    data = UserCreateData(
        full_name="Branch Employee",
        email="employee@example.test",
        role=Role.EMPLOYEE,
        branch_id=12,
        password_hash="stored-password-hash",
    )

    assert data.password_hash == "stored-password-hash"
    assert "stored-password-hash" not in repr(data)
    assert not hasattr(data, "business_id")

    with pytest.raises(ValueError):
        UserCreateData(
            full_name="Owner",
            email="owner@example.test",
            role=Role.OWNER,
            branch_id=12,
            password_hash="stored-password-hash",
        )


def test_user_record_exposes_access_version_but_never_password_data():
    user = UserRecord(
        user_id=4,
        business_id=9,
        branch_id=12,
        full_name="Branch Employee",
        email="employee@example.test",
        role=Role.EMPLOYEE,
        is_active=True,
        access_version=3,
    )

    assert user.access_version == 3
    assert not hasattr(user, "access_version_at_login")
    assert not hasattr(user, "password_hash")


def test_app_installs_no_repository_by_default_and_routes_keep_501():
    app = make_app()
    assert BRANCH_REPOSITORY_EXTENSION not in app.extensions
    assert USER_REPOSITORY_EXTENSION not in app.extensions

    client = app.test_client()
    response = client.post("/api/branches", json={"name": "Downtown"})
    assert response.status_code == 501


def test_repository_injection_is_explicit_and_does_not_activate_unfinished_routes():
    app = make_app()
    branch_repository = FakeBranchRepository()
    user_repository = FakeUserRepository()

    assert install_branch_repository(app, branch_repository) is branch_repository
    assert install_user_repository(app, user_repository) is user_repository
    assert app.extensions[BRANCH_REPOSITORY_EXTENSION] is branch_repository
    assert app.extensions[USER_REPOSITORY_EXTENSION] is user_repository

    response = app.test_client().post("/api/branches", json={"name": "Downtown"})
    assert response.status_code == 501


@pytest.mark.parametrize(
    "installer",
    [install_branch_repository, install_user_repository],
)
def test_installers_reject_incomplete_repository_objects(installer):
    with pytest.raises(TypeError):
        installer(make_app(), object())


@pytest.mark.parametrize(
    ("installer", "fake"),
    [
        (install_branch_repository, FakeBranchRepository()),
        (install_user_repository, FakeUserRepository()),
    ],
)
def test_production_installers_reject_test_fakes(installer, fake):
    app = make_app()
    app.config["APP_ENV"] = "production"

    with pytest.raises(ValueError, match="persistent and shared"):
        installer(app, fake)


def test_production_installer_accepts_only_declared_shared_persistence():
    app = make_app()
    app.config["APP_ENV"] = "production"
    repository = FakeBranchRepository()
    repository.is_persistent = True
    repository.is_shared = True

    assert install_branch_repository(app, repository) is repository
