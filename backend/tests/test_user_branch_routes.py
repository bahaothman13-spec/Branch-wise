"""Focused RBAC and tenant-isolation tests for staff and branch APIs."""

from werkzeug.security import check_password_hash

from app import create_app
from permissions import Role
from repository_contracts import (
    BranchRecord,
    RepositoryNotFoundError,
    UserRecord,
    install_branch_repository,
    install_user_repository,
)

from conftest import make_role_client


class BranchRepositoryFake:
    def __init__(self, business_id=20):
        self.business_id = business_id
        self.created = []

    def list_branches(self, *, actor):
        if actor.principal.business_id != self.business_id:
            return ()
        return (self._record(actor.user_id),)

    def get_branch(self, *, actor, branch_id):
        if actor.principal.business_id != self.business_id or branch_id != 30:
            raise RepositoryNotFoundError()
        return self._record(actor.user_id)

    def create_branch_atomic(self, *, actor, data):
        self.created.append((actor.user_id, data))
        return BranchRecord(31, self.business_id, data.name, data.address, data.phone, data.notes, True, actor.user_id)

    def update_branch_atomic(self, *, actor, branch_id, data):
        if branch_id != 30:
            raise RepositoryNotFoundError()
        current = self._record(actor.user_id)
        values = {key: getattr(current, key) for key in ("name", "address", "phone", "notes")}
        values.update(data.changes)
        return BranchRecord(30, self.business_id, values["name"], values["address"], values["phone"], values["notes"], True, actor.user_id)

    def deactivate_branch_atomic(self, *, actor, branch_id):
        if branch_id != 30:
            raise RepositoryNotFoundError()
        record = self._record(actor.user_id)
        return BranchRecord(30, record.business_id, record.name, record.address, record.phone, record.notes, False, record.created_by)

    def _record(self, creator):
        return BranchRecord(30, self.business_id, "Main", "Address", None, None, True, creator)


class UserRepositoryFake:
    def __init__(self):
        self.created = []

    def list_users(self, *, actor):
        return (UserRecord(40, actor.principal.business_id, 30, "Employee", "employee@example.com", Role.EMPLOYEE, True, 1),)

    def get_own_account(self, **_kwargs):
        raise AssertionError("Account settings are outside this staff test.")

    def update_own_profile_atomic(self, **_kwargs):
        raise AssertionError("Account settings are outside this staff test.")

    def create_user_atomic(self, *, actor, data):
        self.created.append((actor.user_id, data))
        return UserRecord(40, actor.principal.business_id, data.branch_id, data.full_name, data.email, data.role, True, 1)

    def set_user_active_atomic(self, *, actor, user_id, is_active):
        return UserRecord(user_id, actor.principal.business_id, 30, "Employee", "employee@example.com", Role.EMPLOYEE, is_active, 2)

    def assign_user_atomic(self, *, actor, user_id, role, branch_id):
        return UserRecord(user_id, actor.principal.business_id, branch_id, "Employee", "employee@example.com", role, True, 2)

    def change_own_password_atomic(self, **_kwargs):
        raise AssertionError("Account settings are outside this staff test.")


def test_owner_staff_create_hashes_password_and_scopes_active_branch():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    client = make_role_client(app, role=Role.OWNER)
    branch_repository = BranchRepositoryFake()
    user_repository = UserRepositoryFake()
    install_branch_repository(app, branch_repository)
    install_user_repository(app, user_repository)

    response = client.post(
        "/api/users",
        json={
            "name": " New Employee ",
            "email": "Employee@Example.com",
            "role": "Employee",
            "branch_id": 30,
            "initial_password": "private-password",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["data"]["email"] == "employee@example.com"
    stored = user_repository.created[0][1]
    assert check_password_hash(stored.password_hash, "private-password")
    assert "private-password" not in response.get_data(as_text=True)
    assert stored.branch_id == 30


def test_non_owner_cannot_manage_users_or_business_branches():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    client = make_role_client(app, role=Role.BRANCH_MANAGER)
    install_branch_repository(app, BranchRepositoryFake())
    install_user_repository(app, UserRepositoryFake())

    users = client.get("/api/users")
    branches = client.post("/api/branches", json={"name": "Other"})

    assert users.status_code == 403
    assert branches.status_code == 403


def test_user_creation_rejects_a_foreign_business_branch_without_writing():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    client = make_role_client(app, role=Role.OWNER)
    install_branch_repository(app, BranchRepositoryFake(business_id=99))
    users = UserRepositoryFake()
    install_user_repository(app, users)

    response = client.post(
        "/api/users",
        json={
            "name": "Employee",
            "email": "employee@example.com",
            "role": "Employee",
            "branch_id": 30,
            "initial_password": "private-password",
        },
    )

    assert response.status_code == 404
    assert users.created == []
