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

## API coverage and persistence ports

The Flask application factory registers health, authentication, account and business settings, branch and staff management, products/categories/variants, product overview, inventory adjustments, product requests, sales, cancellation requests, and returns APIs. Protected writes require the server-side session cookie plus the matching CSRF cookie and `X-CSRF-Token` header. Permissions and business/branch scope always come from the trusted session and server-loaded repository records.

`repository_contracts.py` defines tenant-scoped ports for branch, user, business settings, shared catalog, and operational transactions. Install adapters explicitly with the matching `install_*_repository` function during application setup. `create_app()` installs no application repository. A missing port returns `501`; the app does not create placeholder records or claim a write succeeded. Operations adapters must provide atomic transactions for domain records, append-only inventory movements, idempotency keys/results, audit effects, and ordered balance locks. Do not mark an in-memory test fake as persistent or install it in production.

No ORM models, application migration scripts, or MySQL schema are included. Those depend on the Database team's approved schema. Until the operational adapter is implemented and installed, APIs that need persistent catalog, inventory, sales, or return data correctly return `501` after authentication, permission, and request validation.

## API contract and Postman

See the repository-level [API_CONTRACT.md](../API_CONTRACT.md) for request and response shapes, permissions, scope rules, and status codes. Import `postman/BranchWise-Backend.postman_collection.json` with `postman/BranchWise-Local.postman_environment.json`. Log in before protected requests; the login request captures the readable CSRF cookie for subsequent writes. Set the sample IDs and `Idempotency-Key` to records/keys from the target test environment.

The Postman collection exercises the route shapes; it does not provide database fixtures and a `501` response means persistence is not installed.

## Tests and migrations

Run the backend suite from this directory:

```powershell
python -m pytest
```

Flask-Migrate is registered as `flask db`. Database models, migration repository, migration scripts, tables, and seed data are intentionally absent until the Database team supplies the approved schema. After that approval, create and review the single migration repository with the Database owner before applying changes to a database.
