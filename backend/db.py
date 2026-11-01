"""Helpers for opening a connection to the BranchWise MySQL database."""

import logging
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv


logger = logging.getLogger(__name__)
ENV_FILE = Path(__file__).resolve().parent / ".env"
REQUIRED_SETTINGS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


def get_db_connection():
    """Return a MySQL connection, or None when settings or connection are unavailable."""
    load_dotenv(dotenv_path=ENV_FILE)

    settings = {name: os.getenv(name) for name in REQUIRED_SETTINGS}
    if any(not value for value in settings.values()):
        logger.warning("Database settings are incomplete; no connection was attempted.")
        return None

    try:
        port = int(os.getenv("DB_PORT", "3306"))
        return mysql.connector.connect(
            host=settings["DB_HOST"],
            port=port,
            user=settings["DB_USER"],
            password=settings["DB_PASSWORD"],
            database=settings["DB_NAME"],
        )
    except (mysql.connector.Error, ValueError):
        # Do not log connector details because they may contain sensitive settings.
        logger.warning("Could not connect to the database; check its settings and availability.")
        return None
