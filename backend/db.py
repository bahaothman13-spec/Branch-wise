"""Shared SQLAlchemy and Flask-Migrate extensions for BranchWise."""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


ENV_FILE = Path(__file__).resolve().parent / ".env"
REQUIRED_SETTINGS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")

db = SQLAlchemy()
migrate = Migrate()


def get_database_uri(environ=None):
    """Build one SQLAlchemy URI from DATABASE_URL or the DB_* settings.

    A memory-only SQLite URI keeps local startup and tests independent of MySQL
    when no database settings have been supplied.
    """
    if environ is None:
        load_dotenv(dotenv_path=ENV_FILE)
        environ = os.environ

    configured_url = environ.get("DATABASE_URL")
    if configured_url:
        try:
            url = make_url(configured_url)
        except ArgumentError:
            raise ValueError("DATABASE_URL is invalid.") from None

        if url.drivername == "mysql":
            url = url.set(drivername="mysql+mysqlconnector")
        elif url.drivername not in {"mysql+mysqlconnector", "sqlite"}:
            raise ValueError(
                "DATABASE_URL must use MySQL Connector/Python or SQLite."
            )
        return url.render_as_string(hide_password=False)

    settings = {name: environ.get(name) for name in REQUIRED_SETTINGS}
    if not any(settings.values()):
        return "sqlite:///:memory:"

    if any(not value for value in settings.values()):
        raise ValueError("Database settings are incomplete.")

    try:
        port = int(environ.get("DB_PORT", "3306"))
    except (TypeError, ValueError):
        raise ValueError("DB_PORT must be an integer between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("DB_PORT must be an integer between 1 and 65535.")

    url = URL.create(
        drivername="mysql+mysqlconnector",
        username=settings["DB_USER"],
        password=settings["DB_PASSWORD"],
        host=settings["DB_HOST"],
        port=port,
        database=settings["DB_NAME"],
    )
    return url.render_as_string(hide_password=False)
