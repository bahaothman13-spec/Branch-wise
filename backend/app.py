from flask import Flask

from routes.auth import auth_bp
from routes.health import health_bp


def create_app():
    """Create the Flask application and register its API routes."""
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
