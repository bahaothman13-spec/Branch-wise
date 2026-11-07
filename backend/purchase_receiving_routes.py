"""Isolated provisional Purchase Receiving API for BE-30 Phase 2.

Structural validation never authorizes receiving or changes orders or stock.
Keep unregistered in the main application until trusted integration is ready.
"""

from flask import Blueprint, jsonify, request

from purchase_receiving_validation import validate_purchase_receiving_input


purchase_receiving_bp = Blueprint("purchase_receiving", __name__, url_prefix="/api")

ALLOWED_FIELDS = {"purchase_order_id", "items"}
ALLOWED_ITEM_FIELDS = {"purchase_order_line_id", "received_quantity"}


@purchase_receiving_bp.post("/purchase-receiving")
def receive_purchase():
    """Validate requests without executing or authorizing purchase receiving."""

    if not request.is_json:
        return jsonify({
            "error": "Request body must be JSON."
        }), 400

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Request body must be a JSON object."
        }), 400

    errors = validate_purchase_receiving_input(data)

    if set(data) - ALLOWED_FIELDS:
        errors["fields"] = "Unknown fields are not allowed."

    items = data.get("items")
    if isinstance(items, list):
        for index, item in enumerate(items):
            if isinstance(item, dict) and set(item) - ALLOWED_ITEM_FIELDS:
                errors[f"items[{index}].fields"] = "Unknown fields are not allowed."

    if errors:
        return jsonify({"errors": errors}), 400

    return jsonify({
        "message": (
            "Purchase receiving is pending authentication, authorization, "
            "business-rule approval, and database integration."
        )
    }), 501
