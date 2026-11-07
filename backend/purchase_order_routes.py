"""Isolated provisional Purchase Order API for BE-29 Phase 2.

Validates input without authentication, database access, or inventory changes.
Keep unregistered in the main application until trusted integration is ready.
"""

from flask import Blueprint, jsonify, request

from purchase_order_validation import validate_purchase_order_input


purchase_order_bp = Blueprint("purchase_orders", __name__, url_prefix="/api")

ALLOWED_FIELDS = {"supplier_id", "branch_id", "items"}
ALLOWED_ITEM_FIELDS = {"product_id", "variant_id", "quantity", "purchase_cost"}


@purchase_order_bp.post("/purchase-orders")
def create_purchase_order():
    """Validate requests without creating or authorizing purchase orders."""

    if not request.is_json:
        return jsonify({
            "error": "Request body must be JSON."
        }), 400

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Request body must be a JSON object."
        }), 400

    errors = validate_purchase_order_input(data)

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
            "Purchase order creation is pending authentication, "
            "authorization, and database integration."
        )
    }), 501
