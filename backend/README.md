# BranchWise Backend Setup

## Install and run

From this directory, create and activate a virtual environment, then install the pinned dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set the database settings for your environment. The `.env` file is ignored by Git. Production must also set `APP_ENV=production`, `SESSION_COOKIE_SECURE=True`, `FLASK_DEBUG=False`, and a randomly generated `SECRET_KEY` of at least 32 bytes. Production startup rejects the in-memory SQLite fallback. Keep real secrets outside version control.

```powershell
flask --app app run
```

The application factory is `create_app`; the module also exposes `app` for Flask CLI use.

## Database configuration

Set `DB_HOST`, `DB_PORT` (optional, defaults to `3306`), `DB_USER`, `DB_PASSWORD`, and `DB_NAME`. The application builds one `mysql+mysqlconnector` SQLAlchemy URL from these values and safely encodes credentials. Alternatively, set `DATABASE_URL`; it takes precedence over the individual settings. A URL may use `mysql+mysqlconnector://` or the shorthand `mysql://` scheme. URL credentials must be percent-encoded.

When neither `DATABASE_URL` nor any `DB_*` values are set, the app uses in-memory SQLite so startup and tests do not need a MySQL service. Supplying only some of the required `DB_*` values is an error. Creating the Flask app initializes the SQLAlchemy engine without opening a network connection.

Use `db` from `db.py` for ORM sessions and `migrate` for the Flask-Migrate CLI. The app no longer exposes the previous direct `mysql.connector` connection helper, so application code has one database connection path.

## Tests and migrations

Run the backend suite from this directory:

```powershell
python -m pytest
```

Flask-Migrate is registered as `flask db`. Database models, migration repository, migration scripts, tables, and seed data are intentionally absent until the Database team supplies the approved schema. After that approval, create and review the single migration repository with the Database owner before applying changes to a database.
