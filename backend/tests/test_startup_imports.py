"""Startup checks deliberately use fresh interpreters without pytest paths."""
import os
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]

def run(*args):
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.update(DATABASE_URL='sqlite:///:memory:', APP_ENV='test', FLASK_DEBUG='0')
    return subprocess.run([sys.executable, *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True, check=True)

def test_database_with_src_first():
    run('-c', "import sys; sys.path.insert(0, 'src'); import db; from pathlib import Path; assert Path(db.__file__).resolve() == Path('db.py').resolve(); assert db.ENV_FILE; assert db.db; assert db.migrate")

def test_application_routes():
    run('-c', "from src.app import app, create_app; expected={'health','auth','account_settings','branches','users','products','category_variants','operations'}; assert set(app.blueprints)==expected, set(app.blueprints); other=create_app(); assert sorted(str(r) for r in app.url_map.iter_rules())==sorted(str(r) for r in other.url_map.iter_rules())")

def test_flask_cli():
    result = run('-m', 'flask', '--app', 'src.app', 'routes')
    assert 'health' in result.stdout
