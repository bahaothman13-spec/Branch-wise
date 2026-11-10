"""Demonstrate provisional BE-29/30 request handling and BE-31 history prep."""

import json
from pathlib import Path
import sys


BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
if str(BACKEND_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIRECTORY))

from flask import Flask

from purchase_history import prepare_purchase_history
from purchase_order_routes import purchase_order_bp
from purchase_receiving_routes import purchase_receiving_bp


def _show_request(client, label, path, payload, expected_status):
    response = client.post(path, json=payload)
    if response.status_code != expected_status:
        raise RuntimeError(
            f"{label} returned HTTP {response.status_code}; "
            f"expected HTTP {expected_status}."
        )
    print(f"{label}: HTTP {response.status_code}")
    print(json.dumps(response.get_json(), indent=2))


def main():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(purchase_order_bp)
    app.register_blueprint(purchase_receiving_bp)

    valid_order = {
        "supplier_id": 1,
        "branch_id": 2,
        "items": [{
            "product_id": 3,
            "quantity": 5,
            "purchase_cost": "12.50",
        }],
    }
    invalid_order = {
        **valid_order,
        "items": [{
            "product_id": 3,
            "quantity": 0,
            "purchase_cost": "12.50",
        }],
    }
    valid_receiving = {
        "purchase_order_id": 10,
        "items": [{
            "purchase_order_line_id": 25,
            "received_quantity": 3,
        }],
    }
    invalid_receiving = {
        **valid_receiving,
        "items": [{
            "purchase_order_line_id": 25,
            "received_quantity": -1,
        }],
    }

    print("Synthetic purchasing integration-readiness demo")
    print("Routes are registered only in this isolated in-memory Flask app.")
    print("\nBE-29 purchase-order request:")
    with app.test_client() as client:
        _show_request(
            client,
            "Valid structure (operation not implemented)",
            "/api/purchase-orders",
            valid_order,
            501,
        )
        _show_request(
            client,
            "Invalid structure",
            "/api/purchase-orders",
            invalid_order,
            400,
        )

        print("\nBE-30 purchase-receiving request:")
        _show_request(
            client,
            "Valid structure (operation not implemented)",
            "/api/purchase-receiving",
            valid_receiving,
            501,
        )
        _show_request(
            client,
            "Invalid structure",
            "/api/purchase-receiving",
            invalid_receiving,
            400,
        )

    synthetic_history = [
        {
            "business_id": 1,
            "branch_id": 2,
            "supplier_id": 3,
            "purchase_order_id": 10,
            "purchase_order_reference": "PO-SYNTHETIC-10",
        },
        {
            "business_id": 1,
            "branch_id": 2,
            "supplier_id": 4,
            "purchase_order_id": 11,
            "purchase_order_reference": "PO-SYNTHETIC-11",
        },
    ]
    prepared_history = prepare_purchase_history(
        synthetic_history,
        business_id=1,
        branch_id=2,
        supplier_id=3,
    )
    print("\nBE-31 supplied synthetic purchase-history records:")
    print(f"Supplier filter: 3")
    print(json.dumps(prepared_history, indent=2))

    print(
        "\nDemo boundary: no purchase orders or receipts are persisted, "
        "and no inventory is read or updated. The supplied history records "
        "are a separate example input, not linked to these requests."
    )


if __name__ == "__main__":
    main()
