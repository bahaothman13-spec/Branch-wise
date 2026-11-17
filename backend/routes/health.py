from flask import Blueprint

from response_helpers import success_response


health_bp = Blueprint("health", __name__, url_prefix="/api")


@health_bp.get("/health")
def health():
    return success_response({"status": "ok"})
