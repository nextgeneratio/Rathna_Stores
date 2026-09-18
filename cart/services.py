"""
Cart service layer — Supabase reads/writes for both guest and authenticated carts.

Identity rules (Phase 3):
- Guest: cart resolved by session_id == session[SESSION_KEY]
- Authenticated: cart resolved by customer_id == session[CUSTOMER_ID_SESSION_KEY]
- Every cart operation calls _resolve_cart_identity(session) to determine which
  column to use; the caller never passes a cart_id or customer_id from the browser.
- A customer must never see or modify another customer's cart, including after
  logout → login as a different user.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Optional, Tuple

from postgrest.exceptions import APIError

from Rathna_Stores.supabase_client import get_supabase_client
from cakes.services import (
    enrich_product,
    format_lkr,
    pick_primary_image,
)

logger = logging.getLogger(__name__)

# ── Table constants ──────────────────────────────────────────────────────────

CARTS_TABLE = "carts"
CART_ITEMS_TABLE = "cart_items"
PRODUCTS_TABLE = "products"
PRODUCT_IMAGES_TABLE = "product_images"

# ── Domain exception ─────────────────────────────────────────────────────────

class CartError(Exception):
    """Raised for user-facing cart validation failures."""

# ── Session keys ─────────────────────────────────────────────────────────────

# Guest cart reference (server-generated UUID stored in session)
SESSION_KEY = "rathna_cart_id"

# Imported by customers/services.py for session coordination
# (the customer session key is owned by customers/services.py)
def _get_customer_id_from_session(session) -> Optional[str]:
    """Return the authenticated customer_id from session, or None."""
    try:
        from customers.services import CUSTOMER_ID_SESSION_KEY
        return session.get(CUSTOMER_ID_SESSION_KEY)
    except Exception:
        return None

# ── Identity resolution ───────────────────────────────────────────────────────

def _resolve_cart_identity(session) -> Tuple[str, str]:
    """
    Return (column_name, value) for looking up / creating the carts row.

    - Authenticated customer → ("customer_id", <UUID string>)
    - Guest                  → ("session_id", <session cart UUID>)

    The value is always server-side derived — never taken from the request.
    """
    customer_id = _get_customer_id_from_session(session)
    if customer_id:
        return ("customer_id", customer_id)

    # Guest: ensure a cart UUID exists in the session
    cart_id = session.get(SESSION_KEY)
    if not cart_id:
        cart_id = str(uuid.uuid4())
        session[SESSION_KEY] = cart_id
        if hasattr(session, "modified"):
            session.modified = True
    return ("session_id", cart_id)

# ── Legacy helper kept for cart merge compatibility ───────────────────────────

def get_or_create_cart_id(session) -> str:
    """
    Return the guest cart_id for this session (legacy / merge helper).
    Creates one if not present. Does not apply to authenticated carts.
    """
    cart_id = session.get(SESSION_KEY)
    if not cart_id:
        cart_id = str(uuid.uuid4())
        session[SESSION_KEY] = cart_id
        if hasattr(session, "modified"):
            session.modified = True
    return cart_id


def clear_cart_session(session) -> None:
    """Remove the guest cart reference from the session."""
    session.pop(SESSION_KEY, None)
    if hasattr(session, "modified"):
        session.modified = True

# ── Cart row helpers ─────────────────────────────────────────────────────────

def _get_or_create_cart_row(col: str, val: str) -> dict:
    """
    Return the carts row matching (col == val), creating it if absent.
    col is either "session_id" or "customer_id".
    Raises CartError on Supabase failure.
    """
    try:
        client = get_supabase_client()
        resp = (
            client.table(CARTS_TABLE)
            .select("cart_id, session_id, customer_id")
            .eq(col, val)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return rows[0]

        # Create new cart row with only the relevant identity column
        insert_resp = (
            client.table(CARTS_TABLE)
            .insert({col: val})
            .execute()
        )
        created = insert_resp.data or []
        if not created:
            raise CartError("Could not create your cart. Please try again.")
        return created[0]
    except APIError as exc:
        logger.error("Cart row fetch/create failed (%s=%s): %s", col, val, exc)
        raise CartError("Cart is temporarily unavailable. Please try again.") from exc


def _fetch_cart_items_raw(cart_row_id: str) -> list[dict]:
    """Return raw cart_items rows joined with product data."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(CART_ITEMS_TABLE)
            .select(
                "cart_item_id, quantity, product_id, "
                "products!inner("
                "  product_id, name, description, price, "
                "  discount_type, discount_value, stock_quantity, "
                "  is_active, category_id, "
                "  categories!inner(category_id, name, slug, is_active)"
                ")"
            )
            .eq("cart_id", cart_row_id)
            .execute()
        )
        return resp.data or []
    except APIError as exc:
        logger.error("Failed to fetch cart items for cart %s: %s", cart_row_id, exc)
        return []


def _fetch_product_images_for_ids(product_ids: list[str]) -> dict[str, Optional[dict]]:
    """Return {product_id: primary_image_dict} for a batch of product IDs."""
    if not product_ids:
        return {}
    try:
        client = get_supabase_client()
        resp = (
            client.table(PRODUCT_IMAGES_TABLE)
            .select("product_id, image_url, alt_text, display_order, is_primary")
            .in_("product_id", product_ids)
            .order("display_order")
            .execute()
        )
        rows = resp.data or []
    except APIError as exc:
        logger.error("Failed to batch-fetch cart product images: %s", exc)
        return {}

    by_product: dict[str, list[dict]] = {}
    for img in rows:
        by_product.setdefault(img["product_id"], []).append(img)

    return {pid: pick_primary_image(imgs) for pid, imgs in by_product.items()}

# ── Public cart read ──────────────────────────────────────────────────────────

def get_cart(session) -> dict:
    """
    Return a fully enriched cart dict for the current session identity.
    Works for both guest (session_id) and authenticated (customer_id) carts.
    Never raises — returns an empty cart on any failure.
    """
    try:
        col, val = _resolve_cart_identity(session)
        cart_row = _get_or_create_cart_row(col, val)
    except (CartError, Exception):
        return _empty_cart("")

    cart_row_id = cart_row["cart_id"]
    raw_items = _fetch_cart_items_raw(cart_row_id)

    if not raw_items:
        return _empty_cart(val)

    product_ids = [item["product_id"] for item in raw_items]
    image_map = _fetch_product_images_for_ids(product_ids)

    enriched_items = []
    subtotal = Decimal("0.00")

    for raw in raw_items:
        product_raw = raw.get("products") or {}
        quantity = int(raw.get("quantity", 1))

        is_active = bool(product_raw.get("is_active", False))
        category = product_raw.get("categories") or {}
        cat_active = bool(category.get("is_active", False))
        stock = int(product_raw.get("stock_quantity", 0))

        is_available = is_active and cat_active and stock > 0
        availability_note = ""
        if not is_active or not cat_active:
            availability_note = "This product is no longer available."
        elif stock <= 0:
            availability_note = "This product is currently out of stock."

        effective_qty = min(quantity, stock) if stock > 0 else quantity

        product_enriched = enrich_product(product_raw)
        unit_price = product_enriched["effective_price"]
        line_total = (unit_price * Decimal(str(effective_qty))).quantize(Decimal("0.01"))

        if is_available:
            subtotal += line_total

        enriched_items.append({
            "cart_item_id": raw["cart_item_id"],
            "product_id": raw["product_id"],
            "product": product_enriched,
            "quantity": quantity,
            "effective_qty": effective_qty,
            "unit_price": unit_price,
            "unit_price_display": format_lkr(unit_price),
            "original_price_display": product_enriched["original_price_display"],
            "has_discount": product_enriched["has_discount"],
            "discount_display": product_enriched["discount_display"],
            "line_total": line_total,
            "line_total_display": format_lkr(line_total),
            "primary_image": image_map.get(raw["product_id"]),
            "is_available": is_available,
            "availability_note": availability_note,
        })

    has_purchasable = any(item["is_available"] for item in enriched_items)

    return {
        "cart_id": val,
        "cart_row_id": cart_row_id,
        "items": enriched_items,
        "subtotal": subtotal,
        "subtotal_display": format_lkr(subtotal),
        "total_items": len(enriched_items),
        "total_units": sum(item["quantity"] for item in enriched_items),
        "has_purchasable_items": has_purchasable,
    }


def _empty_cart(cart_id: str) -> dict:
    return {
        "cart_id": cart_id,
        "cart_row_id": None,
        "items": [],
        "subtotal": Decimal("0.00"),
        "subtotal_display": format_lkr(Decimal("0.00")),
        "total_items": 0,
        "total_units": 0,
        "has_purchasable_items": False,
    }

# ── Cart count (for nav badge) ────────────────────────────────────────────────

def get_cart_unit_count(session) -> int:
    """
    Return total units for the navigation badge.
    Uses the correct identity (customer or guest). Returns 0 on any failure.
    """
    try:
        customer_id = _get_customer_id_from_session(session)
        client = get_supabase_client()

        if customer_id:
            cart_resp = (
                client.table(CARTS_TABLE)
                .select("cart_id, cart_items(quantity)")
                .eq("customer_id", customer_id)
                .limit(1)
                .execute()
            )
        else:
            cart_id = session.get(SESSION_KEY)
            if not cart_id:
                return 0
            cart_resp = (
                client.table(CARTS_TABLE)
                .select("cart_id, cart_items(quantity)")
                .eq("session_id", cart_id)
                .limit(1)
                .execute()
            )

        rows = cart_resp.data or []
        if not rows:
            return 0
        if "cart_items" not in rows[0]:
            items_resp = (
                client.table(CART_ITEMS_TABLE)
                .select("quantity")
                .eq("cart_id", rows[0]["cart_id"])
                .execute()
            )
            return sum(int(item.get("quantity", 0)) for item in (items_resp.data or []))
        return sum(
            int(item.get("quantity", 0))
            for item in (rows[0].get("cart_items") or [])
        )
    except Exception as exc:
        logger.debug("Cart count unavailable: %s", exc)
        return 0

# ── Product validation ────────────────────────────────────────────────────────

def _validate_product_for_cart(product_id: str) -> dict:
    """
    Fetch and validate a product for cart addition.
    Returns the enriched product dict.
    Raises CartError if invalid/inactive/out-of-stock.
    """
    try:
        uuid.UUID(product_id)
    except (ValueError, AttributeError):
        raise CartError("Invalid product.")

    try:
        client = get_supabase_client()
        resp = (
            client.table(PRODUCTS_TABLE)
            .select(
                "product_id, name, price, discount_type, discount_value, "
                "stock_quantity, is_active, category_id, description, "
                "weight_value, weight_unit, serving_count, serving_unit, "
                "categories!inner(category_id, name, slug, is_active)"
            )
            .eq("product_id", product_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
    except APIError as exc:
        logger.error("Product validation fetch failed for %s: %s", product_id, exc)
        raise CartError("Could not verify product. Please try again.") from exc

    if not rows:
        raise CartError("This product is not available.")

    product = rows[0]
    category = product.get("categories") or {}
    if not category.get("is_active", False):
        raise CartError("This product's category is not available.")

    stock = int(product.get("stock_quantity", 0))
    if stock <= 0:
        raise CartError("This product is currently out of stock.")

    return enrich_product(product)

# ── Ownership verification helper ─────────────────────────────────────────────

def _verify_item_ownership(session, cart_item_id: str) -> Tuple[str, str]:
    """
    Verify that cart_item_id belongs to the current session's cart.
    Returns (cart_row_id, product_id).
    Raises CartError if not found / ownership mismatch.
    """
    col, val = _resolve_cart_identity(session)
    if not val:
        raise CartError("Your cart session has expired. Please refresh and try again.")

    client = get_supabase_client()

    cart_resp = (
        client.table(CARTS_TABLE)
        .select("cart_id")
        .eq(col, val)
        .limit(1)
        .execute()
    )
    cart_rows = cart_resp.data or []
    if not cart_rows:
        raise CartError("Cart not found.")
    cart_row_id = cart_rows[0]["cart_id"]

    item_resp = (
        client.table(CART_ITEMS_TABLE)
        .select("cart_item_id, quantity, product_id")
        .eq("cart_item_id", cart_item_id)
        .eq("cart_id", cart_row_id)
        .limit(1)
        .execute()
    )
    items = item_resp.data or []
    if not items:
        raise CartError("Cart item not found.")

    return cart_row_id, items[0]["product_id"]

# ── Add to cart ───────────────────────────────────────────────────────────────

def add_to_cart(session, product_id: str, quantity: int = 1) -> None:
    """
    Add `quantity` units of product_id to the session/customer cart.
    Raises CartError on any validation failure.
    """
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise CartError("Invalid quantity.")
    if quantity < 1:
        raise CartError("Quantity must be at least 1.")

    product = _validate_product_for_cart(product_id)
    stock = int(product.get("stock_quantity", 0))

    col, val = _resolve_cart_identity(session)
    cart_row = _get_or_create_cart_row(col, val)
    cart_row_id = cart_row["cart_id"]

    try:
        client = get_supabase_client()

        existing_resp = (
            client.table(CART_ITEMS_TABLE)
            .select("cart_item_id, quantity")
            .eq("cart_id", cart_row_id)
            .eq("product_id", product_id)
            .limit(1)
            .execute()
        )
        existing = existing_resp.data or []

        if existing:
            current_qty = int(existing[0]["quantity"])
            new_qty = min(current_qty + quantity, stock)
            if new_qty <= current_qty:
                raise CartError(
                    f"You already have the maximum available quantity "
                    f"({stock}) of this item in your cart."
                )
            client.table(CART_ITEMS_TABLE).update(
                {"quantity": new_qty}
            ).eq("cart_item_id", existing[0]["cart_item_id"]).execute()
        else:
            client.table(CART_ITEMS_TABLE).insert({
                "cart_id": cart_row_id,
                "product_id": product_id,
                "quantity": min(quantity, stock),
            }).execute()

    except CartError:
        raise
    except APIError as exc:
        logger.error("Add-to-cart failed for product %s: %s", product_id, exc)
        raise CartError("Could not add item to cart. Please try again.") from exc

# ── Update quantity ───────────────────────────────────────────────────────────

def update_cart_item(session, cart_item_id: str, new_quantity: int) -> None:
    """
    Set the quantity of a cart item. 0 → remove.
    Verifies ownership from session identity before any mutation.
    Raises CartError on validation failure.
    """
    try:
        new_quantity = int(new_quantity)
    except (TypeError, ValueError):
        raise CartError("Invalid quantity.")
    if new_quantity < 0:
        raise CartError("Quantity cannot be negative.")

    try:
        uuid.UUID(cart_item_id)
    except (ValueError, AttributeError):
        raise CartError("Invalid cart item.")

    # Check session has identity
    col, val = _resolve_cart_identity(session)
    if not val:
        raise CartError("Your cart session has expired. Please refresh and try again.")

    try:
        client = get_supabase_client()
        cart_row_id, product_id = _verify_item_ownership(session, cart_item_id)

        if new_quantity == 0:
            client.table(CART_ITEMS_TABLE).delete().eq("cart_item_id", cart_item_id).execute()
            return

        product = _validate_product_for_cart(product_id)
        stock = int(product.get("stock_quantity", 0))
        capped_qty = min(new_quantity, stock)

        client.table(CART_ITEMS_TABLE).update(
            {"quantity": capped_qty}
        ).eq("cart_item_id", cart_item_id).execute()

    except CartError:
        raise
    except APIError as exc:
        logger.error("Update cart item %s failed: %s", cart_item_id, exc)
        raise CartError("Could not update cart. Please try again.") from exc

# ── Remove item ───────────────────────────────────────────────────────────────

def remove_from_cart(session, cart_item_id: str) -> None:
    """
    Remove a single item from the session/customer cart.
    Verifies ownership before deleting.
    """
    try:
        uuid.UUID(cart_item_id)
    except (ValueError, AttributeError):
        raise CartError("Invalid cart item.")

    col, val = _resolve_cart_identity(session)
    if not val:
        raise CartError("Your cart session has expired.")

    try:
        client = get_supabase_client()
        cart_row_id, _ = _verify_item_ownership(session, cart_item_id)
        client.table(CART_ITEMS_TABLE).delete().eq(
            "cart_item_id", cart_item_id
        ).eq("cart_id", cart_row_id).execute()

    except CartError:
        raise
    except APIError as exc:
        logger.error("Remove cart item %s failed: %s", cart_item_id, exc)
        raise CartError("Could not remove item. Please try again.") from exc

# ── Showcase products (for hero) ──────────────────────────────────────────────

def get_showcase_products(limit: int = 5) -> list[dict]:
    """
    Return active products with images for the home-page showcase.
    Ordered by name; products without any image are excluded.
    """
    try:
        client = get_supabase_client()
        resp = (
            client.table(PRODUCTS_TABLE)
            .select(
                "product_id, name, price, discount_type, discount_value, "
                "stock_quantity, is_active, category_id, description, "
                "weight_value, weight_unit, serving_count, serving_unit, "
                "categories!inner(category_id, name, slug, is_active)"
            )
            .eq("is_active", True)
            .eq("categories.is_active", True)
            .order("name")
            .limit(limit)
            .execute()
        )
        products = resp.data or []
    except APIError as exc:
        logger.error("Showcase products fetch failed: %s", exc)
        return []

    if not products:
        return []

    product_ids = [p["product_id"] for p in products]

    try:
        client = get_supabase_client()
        img_resp = (
            client.table(PRODUCT_IMAGES_TABLE)
            .select("product_id, image_url, alt_text, display_order, is_primary")
            .in_("product_id", product_ids)
            .order("display_order")
            .execute()
        )
        img_rows = img_resp.data or []
    except APIError as exc:
        logger.error("Showcase images fetch failed: %s", exc)
        img_rows = []

    by_product: dict[str, list[dict]] = {}
    for img in img_rows:
        by_product.setdefault(img["product_id"], []).append(img)

    result = []
    for p in products:
        imgs = by_product.get(p["product_id"], [])
        primary = pick_primary_image(imgs)
        enriched = enrich_product(p)
        enriched["primary_image"] = primary
        enriched["images"] = imgs
        if primary:
            result.append(enriched)

    return result
