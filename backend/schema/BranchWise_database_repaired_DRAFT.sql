CREATE DATABASE IF NOT EXISTS branchwise
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE branchwise;
SET time_zone = '+00:00';

-- ======BRANCHES=====

-- 1. Every business is an isolated tenant of BranchWise.
CREATE TABLE businesses (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(160) NOT NULL,
    contact_email VARCHAR(254) NULL,
    contact_phone VARCHAR(30) NULL,
    address VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT chk_businesses_nonempty_name CHECK (CHAR_LENGTH(TRIM(name)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2. Branches belong to exactly one business.
-- A branch's creator must be a user in the SAME business.
-- FK to users is added AFTER users exists (circular schema relationship).
CREATE TABLE branches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(120) NOT NULL,
    address VARCHAR(255) NULL,
    phone VARCHAR(30) NULL,
    notes TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_branches_business_id_id (business_id, id),
    UNIQUE KEY uq_branches_business_name (business_id, name),
    KEY idx_branches_creator (business_id, created_by),
    CONSTRAINT chk_branches_nonempty_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    CONSTRAINT fk_branches_business FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3. Users are globally unique by email, scoped to their business.
-- OWNER has no branch; BRANCH_MANAGER/EMPLOYEE must have one.
-- A generated unique owner slot enforces AT MOST one owner per business.
CREATE TABLE users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    branch_id BIGINT UNSIGNED NULL,
    full_name VARCHAR(160) NOT NULL,
    email VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('OWNER', 'BRANCH_MANAGER', 'EMPLOYEE') NOT NULL,
    owner_business_slot BIGINT UNSIGNED GENERATED ALWAYS AS (
        CASE WHEN role = 'OWNER' THEN business_id ELSE NULL END
    ) STORED,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    access_version INT UNSIGNED NOT NULL DEFAULT 1,
    last_password_changed_at DATETIME(6) NULL,
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_email (email),
    UNIQUE KEY uq_users_business_id_id (business_id, id),
    UNIQUE KEY uq_users_owner_business_slot (owner_business_slot),
    KEY idx_users_business_branch (business_id, branch_id),
    KEY idx_users_creator (business_id, created_by),
    CONSTRAINT chk_users_role_branch CHECK (
        (role = 'OWNER' AND branch_id IS NULL) OR
        (role IN ('BRANCH_MANAGER', 'EMPLOYEE') AND branch_id IS NOT NULL)
    ),
    CONSTRAINT chk_users_access_version CHECK (access_version >= 1),
    CONSTRAINT chk_users_nonempty_name CHECK (CHAR_LENGTH(TRIM(full_name)) > 0),
    CONSTRAINT fk_users_business FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_users_branch FOREIGN KEY (business_id, branch_id)
        REFERENCES branches (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_users_creator FOREIGN KEY (business_id, created_by)
        REFERENCES users (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Now that users exists, add the other side of the circular relationship.
ALTER TABLE branches
    ADD CONSTRAINT fk_branches_creator
    FOREIGN KEY (business_id, created_by)
    REFERENCES users (business_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

-- 4. Revocable, server-side sessions. Hash the token before storing it.
CREATE TABLE auth_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    session_token_hash BINARY(32) NOT NULL,
    access_version_at_login INT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    last_seen_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_auth_sessions_token (session_token_hash),
    KEY idx_auth_sessions_user_status (user_id, revoked_at, expires_at),
    KEY idx_auth_sessions_business_user (business_id, user_id),
    CONSTRAINT chk_auth_sessions_expiry CHECK (expires_at > created_at),
    CONSTRAINT fk_auth_sessions_business FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_auth_sessions_user FOREIGN KEY (business_id, user_id)
        REFERENCES users (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5. Append-only audit log of account changes; no password/hash in JSON.
CREATE TABLE user_account_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    target_user_id BIGINT UNSIGNED NOT NULL,
    changed_by_user_id BIGINT UNSIGNED NOT NULL,
    action ENUM('CREATE', 'ROLE_CHANGE', 'BRANCH_CHANGE',
                'ACTIVATE', 'DEACTIVATE', 'PASSWORD_CHANGE') NOT NULL,
    old_values_json JSON NULL,
    new_values_json JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_audit_target (business_id, target_user_id, created_at),
    KEY idx_audit_actor (business_id, changed_by_user_id, created_at),
    CONSTRAINT chk_audit_old_json_object CHECK (
        old_values_json IS NULL OR JSON_TYPE(old_values_json) = 'OBJECT'
    ),
    CONSTRAINT chk_audit_new_json_object CHECK (
        new_values_json IS NULL OR JSON_TYPE(new_values_json) = 'OBJECT'
    ),
    CONSTRAINT fk_audit_business FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_audit_target FOREIGN KEY (business_id, target_user_id)
        REFERENCES users (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_audit_actor FOREIGN KEY (business_id, changed_by_user_id)
        REFERENCES users (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Important app-level rules NOT fully enforced by this schema:
-- * Registration must create business + Owner within ONE transaction.
-- * The first branch is created AFTER Owner signs in.
-- * Only Owner can create branches; authorization belongs in Flask.
-- * At least one ACTIVE Owner must remain for each business.
-- * Update users.access_version on role/branch/password/status changes.
-- * Audit rows should never be updated/deleted; restrict DB privileges accordingly.
-- * Keep app DB connections in UTC; never store plaintext passwords or raw tokens.

USE branchwise;
SET time_zone = '+00:00';

-- 6. Product categories belong to one business.
CREATE TABLE categories (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_categories_business_id_id (business_id, id),
    UNIQUE KEY uq_categories_business_name (business_id, name),
    CONSTRAINT chk_categories_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    CONSTRAINT fk_categories_business FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 7. Products. Product-level unified cost/selling prices.
CREATE TABLE products (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    category_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(180) NOT NULL,
    product_code VARCHAR(80) NOT NULL,
    description TEXT NULL,
    cost_price DECIMAL(14,2) NOT NULL DEFAULT 0,
    selling_price DECIMAL(14,2) NOT NULL,
    minimum_stock INT UNSIGNED NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_products_business_id_id (business_id, id),
    UNIQUE KEY uq_products_business_code (business_id, product_code),
    KEY idx_products_category (business_id, category_id),
    KEY idx_products_creator (business_id, created_by),
    KEY idx_products_name (business_id, name),
    CONSTRAINT chk_products_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
    CONSTRAINT chk_products_code CHECK (CHAR_LENGTH(TRIM(product_code)) > 0),
    CONSTRAINT chk_products_cost CHECK (cost_price >= 0),
    CONSTRAINT chk_products_price CHECK (selling_price >= 0),
    CONSTRAINT fk_products_business FOREIGN KEY (business_id)
        REFERENCES businesses(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_products_category FOREIGN KEY (business_id, category_id)
        REFERENCES categories(business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_products_creator FOREIGN KEY (business_id, created_by)
        REFERENCES users(business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 8. Every sellable product has at least one variant (backend must enforce).
-- Default variant uses is_default=1. At most one default per product.
-- Price overrides in the original master plan are retained as optional fields;
-- BranchWise may choose to keep them NULL to use unified product prices.
CREATE TABLE product_variants (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    product_id BIGINT UNSIGNED NOT NULL,
    variant_code VARCHAR(80) NOT NULL,
    variant_name VARCHAR(120) NOT NULL,
    attributes_json JSON NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    default_product_slot BIGINT UNSIGNED GENERATED ALWAYS AS (
        CASE WHEN is_default = 1 THEN product_id ELSE NULL END
    ) STORED,
    override_cost_price DECIMAL(14,2) NULL,
    override_selling_price DECIMAL(14,2) NULL,
    override_minimum_stock INT UNSIGNED NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_variants_business_id_id (business_id, id),
    UNIQUE KEY uq_variants_business_product_id (business_id, product_id, id),
    UNIQUE KEY uq_variants_business_code (business_id, variant_code),
    UNIQUE KEY uq_variants_default_slot (default_product_slot),
    KEY idx_variants_product (business_id, product_id),
    CONSTRAINT chk_variants_code CHECK (CHAR_LENGTH(TRIM(variant_code)) > 0),
    CONSTRAINT chk_variants_name CHECK (CHAR_LENGTH(TRIM(variant_name)) > 0),
    CONSTRAINT chk_variants_attributes CHECK (
        attributes_json IS NULL OR JSON_TYPE(attributes_json) = 'OBJECT'),
    CONSTRAINT fk_variants_business FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_variants_product FOREIGN KEY (business_id, product_id)
        REFERENCES products (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE branch_inventory (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    branch_id BIGINT UNSIGNED NOT NULL,
    product_variant_id BIGINT UNSIGNED NOT NULL,
    available_qty INT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_branch_inventory_business_id_id (business_id, id),
    UNIQUE KEY uq_branch_inventory_scope (business_id, branch_id, product_variant_id),
    KEY ix_branch_inventory_variant (business_id, product_variant_id, branch_id),
    CONSTRAINT ck_branch_inventory_available_qty CHECK (available_qty >= 0),
    CONSTRAINT fk_branch_inventory_business FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_branch_inventory_branch FOREIGN KEY (business_id, branch_id)
        REFERENCES branches (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_branch_inventory_variant FOREIGN KEY (business_id, product_variant_id)
        REFERENCES product_variants (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ========== 03 SALES ==========

CREATE TABLE sales (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  branch_id BIGINT UNSIGNED NOT NULL,
  sale_reference VARCHAR(40) NOT NULL,
  request_key VARCHAR(120) NOT NULL,
  sold_by_user_id BIGINT UNSIGNED NOT NULL,
  sold_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  subtotal DECIMAL(14,2) NOT NULL,
  total DECIMAL(14,2) NOT NULL,
  status ENUM('COMPLETED','CANCELLED') NOT NULL DEFAULT 'COMPLETED',
  cancelled_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sales_bid_id (business_id,id),
  UNIQUE KEY uq_sales_reference (business_id,sale_reference),
  UNIQUE KEY uq_sales_request (business_id,request_key),
  KEY ix_sales_1 (business_id,branch_id,sold_at,status),
  KEY ix_sales_2 (business_id,sold_by_user_id,sold_at),
  CONSTRAINT ck_sales_totals CHECK (subtotal >= 0 AND total = subtotal),
  CONSTRAINT ck_sales_cancel_state CHECK ((status='COMPLETED' AND cancelled_at IS NULL) OR (status='CANCELLED' AND cancelled_at IS NOT NULL)),
  CONSTRAINT fk_sales_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_sales_branch_id FOREIGN KEY (business_id,branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_sales_sold_by_user_id FOREIGN KEY (business_id,sold_by_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sale_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  sale_id BIGINT UNSIGNED NOT NULL,
  variant_id BIGINT UNSIGNED NOT NULL,
  quantity INT UNSIGNED NOT NULL,
  unit_price DECIMAL(14,2) NOT NULL,
  line_total DECIMAL(14,2) NOT NULL,
  product_name_snapshot VARCHAR(180) NOT NULL,
  variant_code_snapshot VARCHAR(80) NOT NULL,
  unit_cost_at_sale DECIMAL(14,2) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sale_items_bid_id (business_id,id),
  UNIQUE KEY uq_sale_items_variant (sale_id,variant_id),
  UNIQUE KEY uq_sale_items_id_variant (business_id,id,variant_id),
  KEY ix_sale_items_1 (business_id,variant_id,sale_id),
  CONSTRAINT ck_sale_items_qty CHECK (quantity > 0),
  CONSTRAINT ck_sale_items_price CHECK (unit_price >= 0 AND unit_cost_at_sale >= 0 AND line_total = ROUND(quantity * unit_price,2)),
  CONSTRAINT fk_sale_items_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_sale_items_sale_id FOREIGN KEY (business_id,sale_id) REFERENCES sales (business_id,id),
  CONSTRAINT fk_sale_items_variant_id FOREIGN KEY (business_id,variant_id) REFERENCES product_variants (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sale_cancellation_requests (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  sale_id BIGINT UNSIGNED NOT NULL,
  requester_user_id BIGINT UNSIGNED NOT NULL,
  request_reason VARCHAR(500) NOT NULL,
  status ENUM('PENDING','APPROVED','REJECTED') NOT NULL DEFAULT 'PENDING',
  pending_sale_slot BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status='PENDING' THEN sale_id ELSE NULL END) STORED,
  approved_sale_slot BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN status='APPROVED' THEN sale_id ELSE NULL END) STORED,
  reviewer_user_id BIGINT UNSIGNED NULL,
  rejection_reason VARCHAR(500) NULL,
  requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  decided_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sale_cancellation_requests_bid_id (business_id,id),
  UNIQUE KEY uq_sale_cancellation_requests_pending (pending_sale_slot),
  UNIQUE KEY uq_sale_cancellation_requests_approved (approved_sale_slot),
  KEY ix_sale_cancellation_requests_1 (business_id,status,requested_at),
  KEY ix_sale_cancellation_requests_2 (business_id,sale_id),
  CONSTRAINT ck_sale_cancellation_requests_reason CHECK (CHAR_LENGTH(TRIM(request_reason)) > 0),
  CONSTRAINT ck_sale_cancellation_requests_decision CHECK ((status='PENDING' AND reviewer_user_id IS NULL AND decided_at IS NULL AND rejection_reason IS NULL) OR (status='APPROVED' AND reviewer_user_id IS NOT NULL AND decided_at IS NOT NULL AND rejection_reason IS NULL) OR (status='REJECTED' AND reviewer_user_id IS NOT NULL AND decided_at IS NOT NULL AND rejection_reason IS NOT NULL AND CHAR_LENGTH(TRIM(rejection_reason))>0)),
  CONSTRAINT fk_sale_cancellation_requests_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_sale_cancellation_requests_sale_id FOREIGN KEY (business_id,sale_id) REFERENCES sales (business_id,id),
  CONSTRAINT fk_sale_cancellation_requests_requester_user_id FOREIGN KEY (business_id,requester_user_id) REFERENCES users (business_id,id),
  CONSTRAINT fk_sale_cancellation_requests_reviewer_user_id FOREIGN KEY (business_id,reviewer_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ========== 04 SUPPLIERS & PURCHASES ==========

-- 1. Suppliers

CREATE TABLE suppliers (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(160) NOT NULL,
    contact_person VARCHAR(160) NULL,
    phone VARCHAR(30) NULL,
    email VARCHAR(254) NULL,
    address VARCHAR(255) NULL,
    notes TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),

    UNIQUE KEY uq_suppliers_bid_id (business_id, id),
    UNIQUE KEY uq_suppliers_name (business_id, name),

    CONSTRAINT ck_suppliers_name
        CHECK (CHAR_LENGTH(TRIM(name)) > 0),

    CONSTRAINT fk_suppliers_business_id
        FOREIGN KEY (business_id)
        REFERENCES businesses(id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 2. Purchase Orders

CREATE TABLE purchase_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    branch_id BIGINT UNSIGNED NOT NULL,
    supplier_id BIGINT UNSIGNED NOT NULL,
    purchase_reference VARCHAR(40) NOT NULL,

    status ENUM(
        'PENDING',
        'PARTIALLY_RECEIVED',
        'RECEIVED'
    ) NOT NULL DEFAULT 'PENDING',

    ordered_by_user_id BIGINT UNSIGNED NOT NULL,
    ordered_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expected_total_cost DECIMAL(14,2) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),

    UNIQUE KEY uq_purchase_orders_bid_id (business_id, id),
    UNIQUE KEY uq_purchase_orders_reference
        (business_id, purchase_reference),

    KEY ix_purchase_orders_1
        (business_id, branch_id, ordered_at, status),

    KEY ix_purchase_orders_2
        (business_id, supplier_id, ordered_at),

    CONSTRAINT ck_purchase_orders_cost
        CHECK (expected_total_cost >= 0),

    CONSTRAINT fk_purchase_orders_business_id
        FOREIGN KEY (business_id)
        REFERENCES businesses(id),

    CONSTRAINT fk_purchase_orders_branch_id
        FOREIGN KEY (business_id, branch_id)
        REFERENCES branches(business_id, id),

    CONSTRAINT fk_purchase_orders_supplier_id
        FOREIGN KEY (business_id, supplier_id)
        REFERENCES suppliers(business_id, id),

    CONSTRAINT fk_purchase_orders_ordered_by_user_id
        FOREIGN KEY (business_id, ordered_by_user_id)
        REFERENCES users(business_id, id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 3. Purchase Order Items

CREATE TABLE purchase_order_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    purchase_order_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    ordered_qty INT UNSIGNED NOT NULL,
    unit_cost DECIMAL(14,2) NOT NULL,
    line_total DECIMAL(14,2) NOT NULL,

    PRIMARY KEY (id),

    UNIQUE KEY uq_purchase_order_items_bid_id
        (business_id, id),

    UNIQUE KEY uq_purchase_order_items_variant
        (purchase_order_id, variant_id),

    KEY ix_purchase_order_items_1
        (business_id, variant_id),

    CONSTRAINT ck_purchase_order_items_qty
        CHECK (ordered_qty > 0),

    CONSTRAINT ck_purchase_order_items_total
        CHECK (
            unit_cost >= 0
            AND line_total = ROUND(ordered_qty * unit_cost, 2)
        ),

    CONSTRAINT fk_purchase_order_items_business_id
        FOREIGN KEY (business_id)
        REFERENCES businesses(id),

    CONSTRAINT fk_purchase_order_items_purchase_order_id
        FOREIGN KEY (business_id, purchase_order_id)
        REFERENCES purchase_orders(business_id, id),

    CONSTRAINT fk_purchase_order_items_variant_id
        FOREIGN KEY (business_id, variant_id)
        REFERENCES product_variants(business_id, id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 4. Purchase Receipts

CREATE TABLE purchase_receipts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    purchase_order_id BIGINT UNSIGNED NOT NULL,
    receipt_reference VARCHAR(40) NOT NULL,
    request_key VARCHAR(120) NOT NULL,
    received_by_user_id BIGINT UNSIGNED NOT NULL,
    received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    notes VARCHAR(500) NULL,

    PRIMARY KEY (id),

    UNIQUE KEY uq_purchase_receipts_bid_id
        (business_id, id),

    UNIQUE KEY uq_purchase_receipts_reference
        (business_id, receipt_reference),

    UNIQUE KEY uq_purchase_receipts_request
        (business_id, request_key),

    KEY ix_purchase_receipts_1
        (business_id, purchase_order_id, received_at),

    CONSTRAINT fk_purchase_receipts_business_id
        FOREIGN KEY (business_id)
        REFERENCES businesses(id),

    CONSTRAINT fk_purchase_receipts_purchase_order_id
        FOREIGN KEY (business_id, purchase_order_id)
        REFERENCES purchase_orders(business_id, id),

    CONSTRAINT fk_purchase_receipts_received_by_user_id
        FOREIGN KEY (business_id, received_by_user_id)
        REFERENCES users(business_id, id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 5. Purchase Receipt Items

CREATE TABLE purchase_receipt_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    purchase_receipt_id BIGINT UNSIGNED NOT NULL,
    purchase_order_item_id BIGINT UNSIGNED NOT NULL,
    received_qty INT UNSIGNED NOT NULL,

    PRIMARY KEY (id),

    UNIQUE KEY uq_purchase_receipt_items_bid_id
        (business_id, id),

    UNIQUE KEY uq_purchase_receipt_items_line
        (purchase_receipt_id, purchase_order_item_id),

    CONSTRAINT ck_purchase_receipt_items_qty
        CHECK (received_qty > 0),

    CONSTRAINT fk_purchase_receipt_items_business_id
        FOREIGN KEY (business_id)
        REFERENCES businesses(id),

    CONSTRAINT fk_purchase_receipt_items_purchase_receipt_id
        FOREIGN KEY (business_id, purchase_receipt_id)
        REFERENCES purchase_receipts(business_id, id),

    CONSTRAINT fk_purchase_receipt_items_purchase_order_item_id
        FOREIGN KEY (business_id, purchase_order_item_id)
        REFERENCES purchase_order_items(business_id, id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- 	TRANSFER

CREATE TABLE stock_transfers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  transfer_reference VARCHAR(40) NOT NULL,
  request_key VARCHAR(120) NOT NULL,
  source_branch_id BIGINT UNSIGNED NOT NULL,
  destination_branch_id BIGINT UNSIGNED NOT NULL,
  requested_by_user_id BIGINT UNSIGNED NOT NULL,
  status ENUM('PENDING','IN_TRANSIT','COMPLETED','REJECTED','CANCELLED') NOT NULL DEFAULT 'PENDING',
  approved_by_user_id BIGINT UNSIGNED NULL,
  dispatched_by_user_id BIGINT UNSIGNED NULL,
  received_by_user_id BIGINT UNSIGNED NULL,
  requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  dispatched_at DATETIME(6) NULL,
  received_at DATETIME(6) NULL,
  closed_at DATETIME(6) NULL,
  notes VARCHAR(500) NULL,
  event_history_json JSON NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_transfers_bid_id (business_id,id),
  UNIQUE KEY uq_stock_transfers_reference (business_id,transfer_reference),
  UNIQUE KEY uq_stock_transfers_request (business_id,request_key),
  KEY ix_stock_transfers_1 (business_id,source_branch_id,status,requested_at),
  KEY ix_stock_transfers_2 (business_id,destination_branch_id,status,requested_at),
  CONSTRAINT ck_stock_transfers_branches CHECK (source_branch_id <> destination_branch_id),
  CONSTRAINT ck_stock_transfers_history CHECK (event_history_json IS NULL OR JSON_TYPE(event_history_json)='ARRAY'),
  CONSTRAINT fk_stock_transfers_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_stock_transfers_source_branch_id FOREIGN KEY (business_id,source_branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_stock_transfers_destination_branch_id FOREIGN KEY (business_id,destination_branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_stock_transfers_requested_by_user_id FOREIGN KEY (business_id,requested_by_user_id) REFERENCES users (business_id,id),
  CONSTRAINT fk_stock_transfers_approved_by_user_id FOREIGN KEY (business_id,approved_by_user_id) REFERENCES users (business_id,id),
  CONSTRAINT fk_stock_transfers_dispatched_by_user_id FOREIGN KEY (business_id,dispatched_by_user_id) REFERENCES users (business_id,id),
  CONSTRAINT fk_stock_transfers_received_by_user_id FOREIGN KEY (business_id,received_by_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE stock_transfer_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  transfer_id BIGINT UNSIGNED NOT NULL,
  variant_id BIGINT UNSIGNED NOT NULL,
  requested_qty INT UNSIGNED NOT NULL,
  dispatched_qty INT UNSIGNED NOT NULL DEFAULT 0,
  received_qty INT UNSIGNED NOT NULL DEFAULT 0,
  returned_to_source_qty INT UNSIGNED NOT NULL DEFAULT 0,
  written_off_qty INT UNSIGNED NOT NULL DEFAULT 0,
  in_transit_qty INT UNSIGNED NOT NULL DEFAULT 0,
  missing_qty INT UNSIGNED NOT NULL DEFAULT 0,
  resolved_qty INT UNSIGNED NOT NULL DEFAULT 0,
  discrepancy_reported_by BIGINT UNSIGNED NULL,
  discrepancy_reported_at DATETIME(6) NULL,
  discrepancy_resolved_at DATETIME(6) NULL,
  discrepancy_notes TEXT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_stock_transfer_items_bid_id (business_id,id),
  UNIQUE KEY uq_stock_transfer_items_variant (transfer_id,variant_id),
  CONSTRAINT ck_stock_transfer_items_qty CHECK (requested_qty > 0 AND dispatched_qty <= requested_qty),
  CONSTRAINT ck_stock_transfer_items_conservation CHECK (dispatched_qty = received_qty + returned_to_source_qty + written_off_qty + in_transit_qty),
  CONSTRAINT ck_stock_transfer_items_discrepancy CHECK (resolved_qty <= missing_qty AND missing_qty <= dispatched_qty),
  CONSTRAINT ck_stock_transfer_items_discrepancy_report CHECK (missing_qty = 0 OR (discrepancy_reported_at IS NOT NULL AND discrepancy_reported_by IS NOT NULL)),
  CONSTRAINT ck_stock_transfer_items_discrepancy_resolution CHECK (discrepancy_resolved_at IS NULL OR (missing_qty > 0 AND resolved_qty = missing_qty)),
  CONSTRAINT fk_stock_transfer_items_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_stock_transfer_items_transfer_id FOREIGN KEY (business_id,transfer_id) REFERENCES stock_transfers (business_id,id),
  CONSTRAINT fk_stock_transfer_items_variant_id FOREIGN KEY (business_id,variant_id) REFERENCES product_variants (business_id,id),
  CONSTRAINT fk_stock_transfer_items_discrepancy_reported_by FOREIGN KEY (business_id,discrepancy_reported_by) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ========== 06 RETURNS ==========

CREATE TABLE sales_returns (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  branch_id BIGINT UNSIGNED NOT NULL,
  sale_id BIGINT UNSIGNED NULL,
  return_reference VARCHAR(40) NOT NULL,
  request_key VARCHAR(120) NOT NULL,
  external_source_reference VARCHAR(100) NULL,
  return_reason VARCHAR(500) NOT NULL,
  recorded_by_user_id BIGINT UNSIGNED NOT NULL,
  returned_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sales_returns_bid_id (business_id,id),
  UNIQUE KEY uq_sales_returns_reference (business_id,return_reference),
  UNIQUE KEY uq_sales_returns_request (business_id,request_key),
  KEY ix_sales_returns_1 (business_id,branch_id,returned_at),
  KEY ix_sales_returns_2 (business_id,sale_id),
  CONSTRAINT ck_sales_returns_reason CHECK (CHAR_LENGTH(TRIM(return_reason)) > 0),
  CONSTRAINT ck_sales_returns_source CHECK (sale_id IS NOT NULL OR (external_source_reference IS NOT NULL AND CHAR_LENGTH(TRIM(external_source_reference)) > 0)),
  CONSTRAINT fk_sales_returns_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_sales_returns_branch_id FOREIGN KEY (business_id,branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_sales_returns_sale_id FOREIGN KEY (business_id,sale_id) REFERENCES sales (business_id,id),
  CONSTRAINT fk_sales_returns_recorded_by_user_id FOREIGN KEY (business_id,recorded_by_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sale_return_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  return_id BIGINT UNSIGNED NOT NULL,
  sale_item_id BIGINT UNSIGNED NULL,
  variant_id BIGINT UNSIGNED NOT NULL,
  quantity INT UNSIGNED NOT NULL,
  inspection_qty INT UNSIGNED NOT NULL,
  resalable_qty INT UNSIGNED NOT NULL DEFAULT 0,
  damaged_qty INT UNSIGNED NOT NULL DEFAULT 0,
  inspected_by_user_id BIGINT UNSIGNED NULL,
  inspected_at DATETIME(6) NULL,
  inspection_history_json JSON NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sale_return_items_bid_id (business_id,id),
  UNIQUE KEY uq_sale_return_items_variant (return_id,variant_id),
  KEY ix_sale_return_items_1 (business_id,sale_item_id),
  CONSTRAINT ck_sale_return_items_qty CHECK (quantity > 0 AND quantity = inspection_qty + resalable_qty + damaged_qty),
  CONSTRAINT ck_sale_return_items_history CHECK (inspection_history_json IS NULL OR JSON_TYPE(inspection_history_json)='ARRAY'),
  CONSTRAINT ck_sale_return_items_inspection_actor CHECK ((resalable_qty=0 AND damaged_qty=0) OR (inspected_by_user_id IS NOT NULL AND inspected_at IS NOT NULL)),
  CONSTRAINT fk_sale_return_items_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_sale_return_items_return_id FOREIGN KEY (business_id,return_id) REFERENCES sales_returns (business_id,id),
  CONSTRAINT fk_sale_return_items_sale_item_id FOREIGN KEY (business_id,sale_item_id,variant_id) REFERENCES sale_items (business_id,id,variant_id),
  CONSTRAINT fk_sale_return_items_variant_id FOREIGN KEY (business_id,variant_id) REFERENCES product_variants (business_id,id),
  CONSTRAINT fk_sale_return_items_inspected_by_user_id FOREIGN KEY (business_id,inspected_by_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ========== 07 CATALOG APPROVAL ==========

CREATE TABLE catalog_change_requests (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  branch_id BIGINT UNSIGNED NOT NULL,
  requester_user_id BIGINT UNSIGNED NOT NULL,
  request_type ENUM('CREATE_PRODUCT','UPDATE_PRODUCT','CREATE_VARIANT','UPDATE_VARIANT') NOT NULL,
  target_product_id BIGINT UNSIGNED NULL,
  target_variant_id BIGINT UNSIGNED NULL,
  proposed_values_json JSON NOT NULL,
  status ENUM('PENDING','APPROVED','REJECTED') NOT NULL DEFAULT 'PENDING',
  owner_reviewer_id BIGINT UNSIGNED NULL,
  rejection_reason VARCHAR(500) NULL,
  submitted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  decided_at DATETIME(6) NULL,
  manager_reviewer_id BIGINT UNSIGNED NULL,
  manager_comment TEXT NULL,
  manager_reviewed_at DATETIME(6) NULL,
  review_history_json JSON NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_catalog_change_requests_bid_id (business_id,id),
  KEY ix_catalog_change_requests_1 (business_id,branch_id,status,submitted_at),
  KEY ix_catalog_change_requests_2 (business_id,requester_user_id),
  CONSTRAINT ck_catalog_change_requests_targets CHECK ((request_type='CREATE_PRODUCT' AND target_product_id IS NULL AND target_variant_id IS NULL) OR (request_type IN ('UPDATE_PRODUCT','CREATE_VARIANT') AND target_product_id IS NOT NULL AND target_variant_id IS NULL) OR (request_type='UPDATE_VARIANT' AND target_product_id IS NOT NULL AND target_variant_id IS NOT NULL)),
  CONSTRAINT ck_catalog_change_requests_json CHECK (JSON_TYPE(proposed_values_json)='OBJECT'),
  CONSTRAINT ck_catalog_change_requests_decision CHECK ((status='PENDING' AND owner_reviewer_id IS NULL AND decided_at IS NULL AND rejection_reason IS NULL) OR (status='APPROVED' AND owner_reviewer_id IS NOT NULL AND decided_at IS NOT NULL AND rejection_reason IS NULL) OR (status='REJECTED' AND owner_reviewer_id IS NOT NULL AND decided_at IS NOT NULL AND rejection_reason IS NOT NULL AND CHAR_LENGTH(TRIM(rejection_reason))>0)),
  CONSTRAINT ck_catalog_change_requests_history CHECK (review_history_json IS NULL OR JSON_TYPE(review_history_json)='ARRAY'),
  CONSTRAINT fk_catalog_change_requests_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_catalog_change_requests_branch_id FOREIGN KEY (business_id,branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_catalog_change_requests_requester_user_id FOREIGN KEY (business_id,requester_user_id) REFERENCES users (business_id,id),
  CONSTRAINT fk_catalog_change_requests_target_product_id FOREIGN KEY (business_id,target_product_id) REFERENCES products (business_id,id),
  CONSTRAINT fk_catalog_change_requests_target_variant_id FOREIGN KEY (business_id,target_product_id,target_variant_id) REFERENCES product_variants (business_id,product_id,id),
  CONSTRAINT fk_catalog_change_requests_owner_reviewer_id FOREIGN KEY (business_id,owner_reviewer_id) REFERENCES users (business_id,id),
  CONSTRAINT fk_catalog_change_requests_manager_reviewer_id FOREIGN KEY (business_id,manager_reviewer_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ========== 08 AI & DECISIONS ==========

CREATE TABLE ai_forecast_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  branch_id BIGINT UNSIGNED NOT NULL,
  requested_by_user_id BIGINT UNSIGNED NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  history_start DATE NOT NULL,
  history_end DATE NOT NULL,
  provider VARCHAR(80) NULL,
  model_name VARCHAR(120) NULL,
  status ENUM('PENDING','SUCCEEDED','INSUFFICIENT_DATA','FAILED','STALE') NOT NULL DEFAULT 'PENDING',
  input_snapshot_hash CHAR(64) NULL,
  generated_at DATETIME(6) NULL,
  error_code VARCHAR(80) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_ai_forecast_runs_bid_id (business_id,id),
  KEY ix_ai_forecast_runs_1 (business_id,branch_id,generated_at,status),
  CONSTRAINT ck_ai_forecast_runs_periods CHECK (period_start <= period_end AND history_start <= history_end),
  CONSTRAINT fk_ai_forecast_runs_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_ai_forecast_runs_branch_id FOREIGN KEY (business_id,branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_ai_forecast_runs_requested_by_user_id FOREIGN KEY (business_id,requested_by_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ai_forecast_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  forecast_run_id BIGINT UNSIGNED NOT NULL,
  variant_id BIGINT UNSIGNED NOT NULL,
  predicted_demand_qty DECIMAL(14,3) NULL,
  historical_net_sold_qty INT NOT NULL,
  data_quality_status ENUM('SUFFICIENT','INSUFFICIENT') NOT NULL,
  explanation TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  daily_inputs_json JSON NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ai_forecast_items_bid_id (business_id,id),
  UNIQUE KEY uq_ai_forecast_items_variant (forecast_run_id,variant_id),
  UNIQUE KEY uq_ai_forecast_items_id_variant (business_id,id,variant_id),
  CONSTRAINT ck_ai_forecast_items_prediction CHECK ((data_quality_status='INSUFFICIENT' AND predicted_demand_qty IS NULL) OR (data_quality_status='SUFFICIENT' AND predicted_demand_qty IS NOT NULL AND predicted_demand_qty>=0)),
  CONSTRAINT ck_ai_forecast_items_daily_inputs CHECK (daily_inputs_json IS NULL OR JSON_TYPE(daily_inputs_json)='ARRAY'),
  CONSTRAINT fk_ai_forecast_items_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_ai_forecast_items_forecast_run_id FOREIGN KEY (business_id,forecast_run_id) REFERENCES ai_forecast_runs (business_id,id),
  CONSTRAINT fk_ai_forecast_items_variant_id FOREIGN KEY (business_id,variant_id) REFERENCES product_variants (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ai_recommendations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  branch_id BIGINT UNSIGNED NOT NULL,
  variant_id BIGINT UNSIGNED NOT NULL,
  forecast_item_id BIGINT UNSIGNED NULL,
  recommendation_type ENUM('PURCHASE','TRANSFER') NOT NULL,
  suggested_qty INT UNSIGNED NOT NULL,
  source_branch_id BIGINT UNSIGNED NULL,
  suggested_supplier_id BIGINT UNSIGNED NULL,
  stock_snapshot_qty INT UNSIGNED NOT NULL,
  historical_sales_snapshot_qty INT NOT NULL,
  predicted_demand_snapshot_qty DECIMAL(14,3) NULL,
  explanation TEXT NOT NULL,
  supporting_data_json JSON NOT NULL,
  status ENUM('AVAILABLE','STALE') NOT NULL DEFAULT 'AVAILABLE',
  generated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_ai_recommendations_bid_id (business_id,id),
  KEY ix_ai_recommendations_1 (business_id,branch_id,status,generated_at),
  CONSTRAINT ck_ai_recommendations_qty CHECK (suggested_qty > 0 AND (predicted_demand_snapshot_qty IS NULL OR predicted_demand_snapshot_qty >= 0)),
  CONSTRAINT ck_ai_recommendations_type CHECK ((recommendation_type='TRANSFER' AND source_branch_id IS NOT NULL AND source_branch_id<>branch_id AND suggested_supplier_id IS NULL) OR (recommendation_type='PURCHASE' AND source_branch_id IS NULL)),
  CONSTRAINT ck_ai_recommendations_evidence CHECK (JSON_TYPE(supporting_data_json)='OBJECT' AND CHAR_LENGTH(TRIM(explanation))>0),
  CONSTRAINT fk_ai_recommendations_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_ai_recommendations_branch_id FOREIGN KEY (business_id,branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_ai_recommendations_variant_id FOREIGN KEY (business_id,variant_id) REFERENCES product_variants (business_id,id),
  CONSTRAINT fk_ai_recommendations_forecast_item_id FOREIGN KEY (business_id,forecast_item_id,variant_id) REFERENCES ai_forecast_items (business_id,id,variant_id),
  CONSTRAINT fk_ai_recommendations_source_branch_id FOREIGN KEY (business_id,source_branch_id) REFERENCES branches (business_id,id),
  CONSTRAINT fk_ai_recommendations_suggested_supplier_id FOREIGN KEY (business_id,suggested_supplier_id) REFERENCES suppliers (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ai_recommendation_decisions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  business_id BIGINT UNSIGNED NOT NULL,
  recommendation_id BIGINT UNSIGNED NOT NULL,
  decided_by_user_id BIGINT UNSIGNED NOT NULL,
  decision ENUM('ACCEPTED','REJECTED','DEFERRED') NOT NULL,
  reason VARCHAR(500) NULL,
  recommendation_snapshot_json JSON NOT NULL,
  decided_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  client_operation_key VARCHAR(120) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_ai_recommendation_decisions_bid_id (business_id,id),
  UNIQUE KEY uq_ai_recommendation_decisions_client_key (business_id,client_operation_key),
  KEY ix_ai_recommendation_decisions_1 (business_id,recommendation_id,decided_at),
  KEY ix_ai_recommendation_decisions_2 (business_id,decided_by_user_id,decided_at),
  CONSTRAINT ck_ai_recommendation_decisions_snapshot CHECK (JSON_TYPE(recommendation_snapshot_json)='OBJECT'),
  CONSTRAINT fk_ai_recommendation_decisions_business_id FOREIGN KEY (business_id) REFERENCES businesses (id),
  CONSTRAINT fk_ai_recommendation_decisions_recommendation_id FOREIGN KEY (business_id,recommendation_id) REFERENCES ai_recommendations (business_id,id),
  CONSTRAINT fk_ai_recommendation_decisions_decided_by_user_id FOREIGN KEY (business_id,decided_by_user_id) REFERENCES users (business_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ========== 09 INVENTORY MOVEMENT LEDGER ==========
-- Source tables are defined above so their composite foreign keys can be
-- declared inline. The application must keep this ledger append-only.

CREATE TABLE inventory_movements (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_id BIGINT UNSIGNED NOT NULL,
    branch_id BIGINT UNSIGNED NOT NULL,
    product_variant_id BIGINT UNSIGNED NOT NULL,
    movement_type ENUM(
        'SALE',
        'PURCHASE_RECEIPT',
        'SALE_CANCELLATION',
        'RETURN_RESTOCK',
        'TRANSFER_DISPATCH',
        'TRANSFER_RECEIPT',
        'MANUAL_ADJUSTMENT'
    ) NOT NULL,
    quantity_delta BIGINT NOT NULL,
    source_type ENUM(
        'SALE',
        'PURCHASE_RECEIPT',
        'SALE_CANCELLATION',
        'RETURN_INSPECTION',
        'TRANSFER',
        'MANUAL_ADJUSTMENT'
    ) NOT NULL,
    source_reference VARCHAR(255) NOT NULL,
    effect_reference VARCHAR(768) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    origin ENUM('ACTOR', 'SYSTEM') NOT NULL,
    actor_id BIGINT UNSIGNED NULL,
    system_origin VARCHAR(120) NULL,
    reason VARCHAR(500) NULL,
    sale_item_id BIGINT UNSIGNED NULL,
    purchase_receipt_item_id BIGINT UNSIGNED NULL,
    cancellation_request_id BIGINT UNSIGNED NULL,
    transfer_item_id BIGINT UNSIGNED NULL,
    return_item_id BIGINT UNSIGNED NULL,
    transfer_discrepancy_id BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_inventory_movements_business_id_id (business_id, id),
    UNIQUE KEY uq_inventory_movements_effect_reference (effect_reference),
    KEY ix_inventory_movements_balance (
        business_id, branch_id, product_variant_id, created_at
    ),
    KEY ix_inventory_movements_sale_item (business_id, sale_item_id),
    KEY ix_inventory_movements_purchase_receipt_item (
        business_id, purchase_receipt_item_id
    ),
    KEY ix_inventory_movements_cancellation_request (
        business_id, cancellation_request_id
    ),
    KEY ix_inventory_movements_transfer_item (business_id, transfer_item_id),
    KEY ix_inventory_movements_return_item (business_id, return_item_id),
    CONSTRAINT ck_inventory_movements_nonzero CHECK (quantity_delta <> 0),
    CONSTRAINT ck_inventory_movements_sign CHECK (
        (movement_type IN ('SALE', 'TRANSFER_DISPATCH') AND quantity_delta < 0)
        OR (movement_type IN (
            'PURCHASE_RECEIPT',
            'SALE_CANCELLATION',
            'RETURN_RESTOCK',
            'TRANSFER_RECEIPT'
        ) AND quantity_delta > 0)
        OR movement_type = 'MANUAL_ADJUSTMENT'
    ),
    CONSTRAINT ck_inventory_movements_source_type CHECK (
        (movement_type = 'SALE' AND source_type = 'SALE')
        OR (movement_type = 'PURCHASE_RECEIPT' AND source_type = 'PURCHASE_RECEIPT')
        OR (movement_type = 'SALE_CANCELLATION' AND source_type = 'SALE_CANCELLATION')
        OR (movement_type = 'RETURN_RESTOCK' AND source_type = 'RETURN_INSPECTION')
        OR (movement_type IN ('TRANSFER_DISPATCH', 'TRANSFER_RECEIPT')
            AND source_type = 'TRANSFER')
        OR (movement_type = 'MANUAL_ADJUSTMENT'
            AND source_type = 'MANUAL_ADJUSTMENT')
    ),
    CONSTRAINT ck_inventory_movements_origin CHECK (
        (origin = 'ACTOR' AND actor_id IS NOT NULL AND system_origin IS NULL)
        OR (origin = 'SYSTEM' AND actor_id IS NULL
            AND system_origin IS NOT NULL AND CHAR_LENGTH(TRIM(system_origin)) > 0)
    ),
    CONSTRAINT ck_inventory_movements_source_reference CHECK (
        CHAR_LENGTH(TRIM(source_reference)) > 0
        AND CHAR_LENGTH(TRIM(effect_reference)) > 0
    ),
    CONSTRAINT ck_inventory_movements_reason CHECK (
        reason IS NULL OR CHAR_LENGTH(TRIM(reason)) > 0
    ),
    CONSTRAINT ck_inventory_movements_source_record CHECK (
        (source_type = 'SALE' AND sale_item_id IS NOT NULL
            AND purchase_receipt_item_id IS NULL
            AND cancellation_request_id IS NULL AND transfer_item_id IS NULL
            AND return_item_id IS NULL)
        OR (source_type = 'PURCHASE_RECEIPT'
            AND sale_item_id IS NULL AND purchase_receipt_item_id IS NOT NULL
            AND cancellation_request_id IS NULL AND transfer_item_id IS NULL
            AND return_item_id IS NULL)
        OR (source_type = 'SALE_CANCELLATION'
            AND sale_item_id IS NULL AND purchase_receipt_item_id IS NULL
            AND cancellation_request_id IS NOT NULL AND transfer_item_id IS NULL
            AND return_item_id IS NULL)
        OR (source_type = 'TRANSFER'
            AND sale_item_id IS NULL AND purchase_receipt_item_id IS NULL
            AND cancellation_request_id IS NULL AND transfer_item_id IS NOT NULL
            AND return_item_id IS NULL)
        OR (source_type = 'RETURN_INSPECTION'
            AND sale_item_id IS NULL AND purchase_receipt_item_id IS NULL
            AND cancellation_request_id IS NULL AND transfer_item_id IS NULL
            AND return_item_id IS NOT NULL)
        OR (source_type = 'MANUAL_ADJUSTMENT'
            AND sale_item_id IS NULL AND purchase_receipt_item_id IS NULL
            AND cancellation_request_id IS NULL AND transfer_item_id IS NULL
            AND return_item_id IS NULL)
    ),
    CONSTRAINT ck_inventory_movements_legacy_discrepancy
        CHECK (transfer_discrepancy_id IS NULL),
    CONSTRAINT fk_movements_business_id FOREIGN KEY (business_id)
        REFERENCES businesses (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_branch_id FOREIGN KEY (business_id, branch_id)
        REFERENCES branches (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_variant_id FOREIGN KEY (business_id, product_variant_id)
        REFERENCES product_variants (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_branch_inventory FOREIGN KEY (
        business_id, branch_id, product_variant_id
    ) REFERENCES branch_inventory (business_id, branch_id, product_variant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_actor_user_id FOREIGN KEY (business_id, actor_id)
        REFERENCES users (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_sale_item_id FOREIGN KEY (business_id, sale_item_id)
        REFERENCES sale_items (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_purchase_receipt_item_id
        FOREIGN KEY (business_id, purchase_receipt_item_id)
        REFERENCES purchase_receipt_items (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_cancellation_request_id
        FOREIGN KEY (business_id, cancellation_request_id)
        REFERENCES sale_cancellation_requests (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_transfer_item_id
        FOREIGN KEY (business_id, transfer_item_id)
        REFERENCES stock_transfer_items (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_movements_return_item_id FOREIGN KEY (business_id, return_item_id)
        REFERENCES sale_return_items (business_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

