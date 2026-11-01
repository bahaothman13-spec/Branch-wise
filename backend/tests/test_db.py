"""Unit tests for database connection setup without a MySQL server."""

from unittest.mock import Mock

import mysql.connector
import pytest

import db


@pytest.fixture(autouse=True)
def isolated_database_settings(monkeypatch, tmp_path):
    """Use a temporary .env file and keep developer settings out of these tests."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(db, "ENV_FILE", env_file)
    for name in (*db.REQUIRED_SETTINGS, "DB_PORT"):
        monkeypatch.delenv(name, raising=False)
    return env_file


def test_get_db_connection_reads_dotenv_and_returns_connection(
    isolated_database_settings, monkeypatch
):
    isolated_database_settings.write_text(
        "DB_HOST=db.example.test\n"
        "DB_PORT=3307\n"
        "DB_USER=test_user\n"
        "DB_PASSWORD=test_password\n"
        "DB_NAME=test_database\n",
        encoding="utf-8",
    )
    connection = Mock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(db.mysql.connector, "connect", connect)

    result = db.get_db_connection()

    assert result is connection
    connect.assert_called_once_with(
        host="db.example.test",
        port=3307,
        user="test_user",
        password="test_password",
        database="test_database",
    )


def test_get_db_connection_returns_none_when_settings_are_missing(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(db.mysql.connector, "connect", connect)

    assert db.get_db_connection() is None
    connect.assert_not_called()


def test_get_db_connection_handles_connector_errors_without_logging_secrets(
    isolated_database_settings, monkeypatch, caplog
):
    isolated_database_settings.write_text(
        "DB_HOST=db.example.test\n"
        "DB_USER=test_user\n"
        "DB_PASSWORD=private_password\n"
        "DB_NAME=test_database\n",
        encoding="utf-8",
    )
    connect = Mock(side_effect=mysql.connector.Error("private_password was rejected"))
    monkeypatch.setattr(db.mysql.connector, "connect", connect)

    assert db.get_db_connection() is None
    assert "private_password" not in caplog.text


def test_get_db_connection_handles_invalid_port_without_connecting(
    monkeypatch,
):
    monkeypatch.setenv("DB_HOST", "db.example.test")
    monkeypatch.setenv("DB_USER", "test_user")
    monkeypatch.setenv("DB_PASSWORD", "test_password")
    monkeypatch.setenv("DB_NAME", "test_database")
    monkeypatch.setenv("DB_PORT", "not-a-port")
    connect = Mock()
    monkeypatch.setattr(db.mysql.connector, "connect", connect)

    assert db.get_db_connection() is None
    connect.assert_not_called()


def test_creating_flask_app_does_not_connect_to_database(monkeypatch):
    from app import create_app

    connect = Mock()
    monkeypatch.setattr(db.mysql.connector, "connect", connect)

    create_app()

    connect.assert_not_called()
