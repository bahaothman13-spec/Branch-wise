from flask import Flask
from werkzeug.exceptions import HTTPException

from routes.auth import auth_bp
from routes.branches import branches_bp
from routes.health import health_bp
from routes.users import users_bp
from response_helpers import error_response


def create_app():
    """Create the Flask application and register its API routes."""
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(branches_bp)
    app.register_blueprint(users_bp)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        """Return generic JSON for routing and HTTP errors without internals."""
        status_code = error.code or 500
        messages = {
            400: "Invalid request.",
            401: "Authentication is required.",
            403: "Permission denied.",
            404: "Resource not found.",
            405: "Method not allowed.",
            409: "The request conflicts with the current resource state.",
            422: "Request validation failed.",
            501: "This operation is not implemented.",
        }
        return error_response(
            messages.get(status_code, "The request could not be completed."),
            status_code=status_code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(_error):
        """Hide exception details and credentials from API clients."""
        return error_response(
            "An unexpected server error occurred.", status_code=500
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
