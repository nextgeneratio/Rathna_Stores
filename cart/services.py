"""
Cart service layer — all Supabase reads/writes for guest-session cart operations.

Design rules:
- Guest carts are keyed by a server-generated Django session key (never an
  arbitrary client-supplied cart ID).
- Every mutation re-validates the product against current active stock.
- All money uses Decimal; totals are computed at request time, never cached.
- Failures surface as user-safe CartError exceptions; callers convert to
  Django messages. Technical detail is logged server-side only.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Optional

from postgrest.exceptions import APIError

from Rathna_Stores.supabase_client import get_supabase_client
from cakes.services import (
    enrich_product,
    calculate_effective_price,
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


# ── Session helpers ──────────────────────────────────────────────────────────

SESSION_KEY = "rathna_cart_id"


def get_or_create_cart_id(session) -> str:
    """
    Return the cart_id for this session.

    The cart_id is stored in the Django session (server-side) and is used as
    the `session_id` on the Supabase `carts` row.  The browser never sees it
    directly — it cannot be supplied or overridden via a query parameter or
    POST body.
    """
    cart_id = session.get(SESSION_KEY)
    if not cart_id:
        cart_id = str(uuid.uuid4())
        session[SESSION_KEY] = cart_id
        if hasattr(session, "modified"):
            session.modified = True
    return cart_id


def clear_cart_session(session) -> None:
    """Remove the cart reference from the session (e.g., after checkout)."""
    session.pop(SESSION_KEY, None)
    if hasattr(session, "modified"):
        session.modified = True


# ── Cart / row fetching ──────────────────────────────────────────────────────

def _get_or_create_cart_row(cart_id: str) -> dict:
    """
    Return the `carts` row for cart_id, creating it if it doesn't exist.
    Raises CartError on Supabase failure.
    """
    try:
        client = get_supabase_client()
        resp = (
            client.table(CARTS_TABLE)
            .select("cart_id, session_id")
            .eq("session_id", cart_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return rows[0]

        # Create new cart row
        insert_resp = (
            client.table(CARTS_TABLE)
            .insert({"session_id": cart_id})
            .execute()
        )
        created = insert_resp.data or []
        if not created:
            raise CartError("Could not create your cart. Please try again.")
        return created[0]
    except APIError as exc:
        logger.error("Cart row fetch/create failed for session %s: %s", cart_id, exc)
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


# ── Public cart read ─────────────────────────────────────────────────────────

def get_cart(session) -> dict:
    """
    Return a fully enriched cart dict for the current session.

    Return structure:
    {
        "cart_id": str,
        "items": [
            {
                "cart_item_id": str,
                "product_id": str,
                "product": enriched product dict,
                "quantity": int,
                "unit_price": Decimal,       # effective price at render time
                "unit_price_display": str,
                "original_price_display": str,
                "has_discount": bool,
                "discount_display": str,
                "line_total": Decimal,
                "line_total_display": str,
                "primary_image": dict|None,
                "is_available": bool,        # False when inactive or 0 stock
                "availability_note": str,    # message to show when unavailable
            },
            ...
        ],
        "subtotal": Decimal,
        "subtotal_display": str,
        "total_items": int,               # number of distinct products
        "total_units": int,               # sum of all quantities
        "has_purchasable_items": bool,
    }
    """
    cart_id = get_or_create_cart_id(session)

    try:
        cart_row = _get_or_create_cart_row(cart_id)
    except (CartError, Exception):
        # Return an empty-cart structure so the page still renders
        return _empty_cart(cart_id)

    cart_row_id = cart_row["cart_id"]
    raw_items = _fetch_cart_items_raw(cart_row_id)

    if not raw_items:
        return _empty_cart(cart_id)

    # Batch-fetch images
    product_ids = [item["product_id"] for item in raw_items]
    image_map = _fetch_product_images_for_ids(product_ids)

    enriched_items = []
    subtotal = Decimal("0.00")

    for raw in raw_items:
        product_raw = raw.get("products") or {}
        quantity = int(raw.get("quantity", 1))

        # Re-validate product state at render time
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

        # Cap quantity to stock (render-time only; mutation is capped separately)
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
        "cart_id": cart_id,
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
    Return total units in cart for the navigation badge.
    Returns 0 on any failure — must never make a page unusable.
    """
    cart_id = session.get(SESSION_KEY)
    if not cart_id:
        return 0
    try:
        client = get_supabase_client()
        # Get the carts row
        cart_resp = (
            client.table(CARTS_TABLE)
            .select("cart_id")
            .eq("session_id", cart_id)
            .limit(1)
            .execute()
        )
        rows = cart_resp.data or []
        if not rows:
            return 0
        cart_row_id = rows[0]["cart_id"]

        # Sum quantities
        items_resp = (
            client.table(CART_ITEMS_TABLE)
            .select("quantity")
            .eq("cart_id", cart_row_id)
            .execute()
        )
        items = items_resp.data or []
        return sum(int(i.get("quantity", 0)) for i in items)
    except Exception as exc:
        logger.debug("Cart count unavailable: %s", exc)
        return 0


# ── Validate product for cart addition ──────────────────────────────────────

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


# ── Add to cart ──────────────────────────────────────────────────────────────

def add_to_cart(session, product_id: str, quantity: int = 1) -> None:
    """
    Add `quantity` units of product_id to the session cart.
    Raises CartError on any validation failure.
    """
    # Validate quantity
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise CartError("Invalid quantity.")
    if quantity < 1:
        raise CartError("Quantity must be at least 1.")

    product = _validate_product_for_cart(product_id)
    stock = int(product.get("stock_quantity", 0))

    cart_id = get_or_create_cart_id(session)
    cart_row = _get_or_create_cart_row(cart_id)
    cart_row_id = cart_row["cart_id"]

    try:
        client = get_supabase_client()

        # Check existing quantity
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
            new_qty = current_qty + quantity
            # Cap to stock
            if new_qty > stock:
                new_qty = stock
                if new_qty <= current_qty:
                    raise CartError(
                        f"You already have the maximum available quantity "
                        f"({stock}) of this item in your cart."
                    )
            client.table(CART_ITEMS_TABLE).update(
                {"quantity": new_qty}
            ).eq("cart_item_id", existing[0]["cart_item_id"]).execute()
        else:
            # Cap new quantity to stock
            capped_qty = min(quantity, stock)
            client.table(CART_ITEMS_TABLE).insert(
                {
                    "cart_id": cart_row_id,
                    "product_id": product_id,
                    "quantity": capped_qty,
                }
            ).execute()

    except CartError:
        raise
    except APIError as exc:
        logger.error("Add-to-cart failed for product %s: %s", product_id, exc)
        raise CartError("Could not add item to cart. Please try again.") from exc


# ── Update quantity ──────────────────────────────────────────────────────────

def update_cart_item(session, cart_item_id: str, new_quantity: int) -> None:
    """
    Set the quantity of a cart item.
    - new_quantity == 0  → remove the row.
    - new_quantity > stock → capped to stock.
    Raises CartError on validation failure.
    """
    try:
        new_quantity = int(new_quantity)
    except (TypeError, ValueError):
        raise CartError("Invalid quantity.")
    if new_quantity < 0:
        raise CartError("Quantity cannot be negative.")

    # Validate UUID
    try:
        uuid.UUID(cart_item_id)
    except (ValueError, AttributeError):
        raise CartError("Invalid cart item.")

    cart_id = session.get(SESSION_KEY)
    if not cart_id:
        raise CartError("Your cart session has expired. Please refresh and try again.")

    try:
        client = get_supabase_client()

        # Fetch the item and verify it belongs to this session's cart
        cart_resp = (
            client.table(CARTS_TABLE)
            .select("cart_id")
            .eq("session_id", cart_id)
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

        product_id = items[0]["product_id"]

        if new_quantity == 0:
            client.table(CART_ITEMS_TABLE).delete().eq(
                "cart_item_id", cart_item_id
            ).execute()
            return

        # Validate product still available
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


# ── Remove item ──────────────────────────────────────────────────────────────

def remove_from_cart(session, cart_item_id: str) -> None:
    """
    Remove a single item from the session cart.
    Verifies the item belongs to this session before deleting.
    """
    try:
        uuid.UUID(cart_item_id)
    except (ValueError, AttributeError):
        raise CartError("Invalid cart item.")

    cart_id = session.get(SESSION_KEY)
    if not cart_id:
        raise CartError("Your cart session has expired.")

    try:
        client = get_supabase_client()

        # Verify ownership
        cart_resp = (
            client.table(CARTS_TABLE)
            .select("cart_id")
            .eq("session_id", cart_id)
            .limit(1)
            .execute()
        )
        cart_rows = cart_resp.data or []
        if not cart_rows:
            raise CartError("Cart not found.")
        cart_row_id = cart_rows[0]["cart_id"]

        client.table(CART_ITEMS_TABLE).delete().eq(
            "cart_item_id", cart_item_id
        ).eq("cart_id", cart_row_id).execute()

    except CartError:
        raise
    except APIError as exc:
        logger.error("Remove cart item %s failed: %s", cart_item_id, exc)
        raise CartError("Could not remove item. Please try again.") from exc


# ── Showcase products (for hero) ─────────────────────────────────────────────

def get_showcase_products(limit: int = 5) -> list[dict]:
    """
    Return a small deterministic set of active products with images
    for the home-page showcase.  Ordered by name; products without
    a primary/gallery image are excluded.
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
        # Only include products that have at least one image
        if primary:
            result.append(enriched)

    return result
