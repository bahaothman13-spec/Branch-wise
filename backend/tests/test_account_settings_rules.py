"""Tests for pure business and self-account settings rules."""

import pytest

from account_settings_rules import (
    AccountRecord,
    BusinessSettingsProposal,
    BusinessSettingsRecord,
    PasswordChangeProposal,
    ProfileUpdateProposal,
    validate_business_settings_update,
    validate_password_change,
    validate_profile_update,
    view_business_settings,
    view_personal_account,
)
from permissions import (
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    Role,
)
from security import hash_password, verify_password


BUSINESS_ID = 10
USER_ID = 23


def principal(
    role=Role.OWNER, business_id=BUSINESS_ID, branch_id=None, permissions=None
):
    if role is not Role.OWNER and branch_id is None:
        branch_id = 22
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=branch_id,
        permissions=(
            ROLE_PERMISSIONS[role] if permissions is None else frozenset(permissions)
        ),
    )


def business(**changes):
    values = {
        "business_id": BUSINESS_ID,
        "business_name": "BranchWise Market",
        "email": "owner@example.com",
    }
    values.update(changes)
    return BusinessSettingsRecord(**values)


def account(*, role=Role.OWNER, business_id=BUSINESS_ID, user_id=USER_ID, **changes):
    branch_id = None if role is Role.OWNER else 22
    values = {
        "user_id": user_id,
        "business_id": business_id,
        "name": "Test Owner",
        "email": "owner@example.com",
        "role": role,
        "branch_id": branch_id,
        "is_active": True,
        "password_hash": hash_password("current-password"),
    }
    values.update(changes)
    return AccountRecord(**values)


def test_owner_can_view_and_propose_updates_to_own_business_settings():
    owner = principal()
    settings = business(business_name="  BranchWise Market  ")

    view = view_business_settings(owner, settings)
    proposal = validate_business_settings_update(
        owner,
        settings,
        {"business_name": "  New Market  ", "email": " contact@example.com "},
    )

    assert view.business_name == "BranchWise Market"
    assert view.email == "owner@example.com"
    assert proposal == BusinessSettingsProposal(
        changes={"business_name": "New Market", "email": "contact@example.com"}
    )


@pytest.mark.parametrize(
    "actor",
    [
        principal(Role.BRANCH_MANAGER),
        principal(Role.EMPLOYEE),
        principal(permissions=set()),
    ],
)
def test_only_authorized_owner_can_modify_business_settings(actor):
    with pytest.raises(PermissionError):
        validate_business_settings_update(actor, business(), {"business_name": "Other"})


def test_business_settings_reads_and_updates_are_restricted_to_same_business():
    owner = principal()
    foreign_business = business(business_id=99, business_name="Private Business")

    with pytest.raises(PermissionError):
        view_business_settings(owner, foreign_business)
    with pytest.raises(PermissionError):
        validate_business_settings_update(
            owner, foreign_business, {"business_name": "Changed"}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"business_name": "  "},
        {"business_name": 10},
        {"email": "not-an-email"},
        {"business_id": 99},
        {"phone": "+962 6 555 0100"},
    ],
)
def test_business_settings_reject_empty_invalid_or_unsupported_changes(changes):
    with pytest.raises(ValueError):
        validate_business_settings_update(principal(), business(), changes)


def test_malformed_business_record_is_rejected():
    with pytest.raises(ValueError):
        view_business_settings(principal(), business(business_id=True))


def test_profile_update_accepts_only_trimmed_name_and_valid_email():
    actor = principal()
    current = account()

    proposal = validate_profile_update(
        actor,
        USER_ID,
        current,
        {"name": "  New Name  ", "email": " new@example.com "},
    )

    assert proposal == ProfileUpdateProposal(
        changes={"name": "New Name", "email": "new@example.com"}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": "   "},
        {"name": 123},
        {"email": "invalid-email"},
    ],
)
def test_profile_update_rejects_invalid_changes(changes):
    with pytest.raises(ValueError):
        validate_profile_update(principal(), USER_ID, account(), changes)


@pytest.mark.parametrize(
    "protected_change",
    [
        {"role": Role.EMPLOYEE},
        {"business_id": 99},
        {"branch_id": 55},
        {"is_active": False},
        {"password_hash": "plain-text"},
    ],
)
def test_profile_update_rejects_be12_and_sensitive_fields(protected_change):
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_profile_update(principal(), USER_ID, account(), protected_change)


def test_profile_access_requires_matching_authenticated_user_and_business_context():
    with pytest.raises(PermissionError):
        view_personal_account(principal(), USER_ID + 1, account())
    with pytest.raises(PermissionError):
        validate_profile_update(
            principal(), USER_ID, account(business_id=99), {"name": "Other"}
        )


def test_personal_account_view_contains_only_safe_permitted_account_fields():
    record = account(password_hash="sensitive-hash")

    view = view_personal_account(principal(), USER_ID, record)

    assert view.user_id == USER_ID
    assert view.name == "Test Owner"
    assert view.email == "owner@example.com"
    assert view.role is Role.OWNER
    assert view.business_id == BUSINESS_ID
    assert view.branch_id is None
    assert view.is_active is True
    assert not hasattr(view, "password_hash")
    assert "sensitive-hash" not in repr(view)


@pytest.mark.parametrize(
    "malformed",
    [
        {"user_id": True},
        {"business_id": 0},
        {"email": "bad-email"},
        {"branch_id": 5},
        {"is_active": 1},
        {"password_hash": ""},
    ],
)
def test_malformed_trusted_account_records_are_rejected(malformed):
    with pytest.raises(ValueError):
        view_personal_account(principal(), USER_ID, account(**malformed))


def test_password_change_verifies_current_password_and_returns_only_a_secure_hash():
    current = account()
    proposal = validate_password_change(
        principal(),
        USER_ID,
        current,
        current_password="current-password",
        new_password="replacement-password",
        confirm_password="replacement-password",
    )

    assert isinstance(proposal, PasswordChangeProposal)
    assert proposal.password_hash != "replacement-password"
    assert verify_password("replacement-password", proposal.password_hash)
    assert "replacement-password" not in repr(proposal)


def test_password_change_rejects_wrong_current_password_without_echoing_secrets():
    current = account()

    with pytest.raises(PermissionError) as error:
        validate_password_change(
            principal(),
            USER_ID,
            current,
            current_password="wrong-current-password",
            new_password="replacement-password",
            confirm_password="replacement-password",
        )

    assert "wrong-current-password" not in str(error.value)
    assert "replacement-password" not in str(error.value)


@pytest.mark.parametrize(
    "password_fields",
    [
        ("current-password", "short", "short"),
        ("current-password", "replacement-password", "mismatch"),
        ("", "replacement-password", "replacement-password"),
    ],
)
def test_password_change_rejects_invalid_input(password_fields):
    current_password, new_password, confirm_password = password_fields
    with pytest.raises(ValueError):
        validate_password_change(
            principal(),
            USER_ID,
            account(),
            current_password=current_password,
            new_password=new_password,
            confirm_password=confirm_password,
        )
