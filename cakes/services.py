"""
Catalogue service layer — all Supabase data access for the cakes app.

This module is the single point of contact between Django views and Supabase.
Views must not reference table names, column names, or storage paths directly.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from postgrest.exceptions import APIError

from Rathna_Stores.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRODUCTS_TABLE = "products"
CATEGORIES_TABLE = "categories"
PRODUCT_IMAGES_TABLE = "product_images"
REVIEWS_TABLE = "reviews"
CUSTOMERS_TABLE = "customers"

STORAGE_BUCKET = "cakes"

# Price-range presets: (label, min_inclusive, max_inclusive_or_None)
PRICE_PRESETS = [
    ("under-1500", "Under LKR 1,500", Decimal("0"), Decimal("1499.99")),
    ("1500-3000", "LKR 1,500 – 3,000", Decimal("1500"), Decimal("3000")),
    ("3000-5000", "LKR 3,000 – 5,000", Decimal("3000.01"), Decimal("5000")),
    ("over-5000", "Over LKR 5,000", Decimal("5000.01"), None),
]

# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


def calculate_effective_price(price: Decimal, discount_type: str, discount_value: Decimal) -> Decimal:
    """
    Return the discounted sale price, rounded to 2 decimal places.

    Rules (mirrors database CHECK constraints):
      - NONE        → effective price == price
      - PERCENT     → price * (1 - discount_value/100)   [1 ≤ discount_value ≤ 100]
      - AMOUNT      → price - discount_value              [discount_value > 0]
    """
    price = Decimal(str(price))
    discount_value = Decimal(str(discount_value))

    if discount_type == "PERCENT":
        factor = 1 - discount_value / Decimal("100")
        effective = (price * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif discount_type == "AMOUNT":
        effective = (price - discount_value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:  # NONE
        effective = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Guard: effective price should never be negative
    return max(effective, Decimal("0.00"))


def format_lkr(amount: Decimal) -> str:
    """Format a Decimal as 'LKR 3,800' — no false decimal places for whole values."""
    amount = Decimal(str(amount))
    if amount == amount.to_integral_value():
        return "LKR {:,}".format(int(amount))
    return "LKR {:,.2f}".format(amount)


def enrich_product(product: dict) -> dict:
    """
    Add computed fields to a raw product dict from Supabase:
      - effective_price (Decimal)
      - effective_price_display (str, LKR formatted)
      - original_price_display (str, LKR formatted)
      - has_discount (bool)
      - discount_display (str, e.g. '10% off' or 'LKR 200 off')
      - in_stock (bool)
      - primary_image (dict or None) — taken from pre-fetched images if present
    """
    price = Decimal(str(product.get("price", "0")))
    discount_type = product.get("discount_type", "NONE")
    discount_value = Decimal(str(product.get("discount_value", "0")))

    effective = calculate_effective_price(price, discount_type, discount_value)
    has_discount = discount_type != "NONE" and discount_value > 0

    product = dict(product)  # avoid mutating caller's dict
    product["effective_price"] = effective
    product["effective_price_display"] = format_lkr(effective)
    product["original_price_display"] = format_lkr(price)
    product["has_discount"] = has_discount
    product["in_stock"] = int(product.get("stock_quantity", 0)) > 0

    if has_discount:
        if discount_type == "PERCENT":
            product["discount_display"] = "{}% off".format(int(discount_value))
        else:
            product["discount_display"] = "{} off".format(format_lkr(discount_value))
    else:
        product["discount_display"] = ""

    # primary_image populated separately via enrich_products_with_images()
    product.setdefault("primary_image", None)
    product.setdefault("images", [])
    return product


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def pick_primary_image(images: list[dict]) -> Optional[dict]:
    """Return the primary image dict, or the first by display_order, or None."""
    if not images:
        return None
    for img in images:
        if img.get("is_primary"):
            return img
    return images[0]


def get_image_public_url(storage_path: str) -> str:
    """
    Return a stable public URL for a cakes-bucket storage object.
    Falls back to the path itself on error (caller handles missing images).
    """
    try:
        client = get_supabase_client()
        result = client.storage.from_(STORAGE_BUCKET).get_public_url(storage_path)
        return result
    except Exception as exc:
        logger.warning("Could not get public URL for %s: %s", storage_path, exc)
        return storage_path


# ---------------------------------------------------------------------------
# Category queries
# ---------------------------------------------------------------------------


def get_active_categories() -> list[dict]:
    """Return all active categories ordered by name."""
    try:
        client = get_supabase_client()
        response = (
            client.table(CATEGORIES_TABLE)
            .select("category_id, name, slug, description, is_active")
            .eq("is_active", True)
            .order("name")
            .execute()
        )
        return response.data or []
    except APIError as exc:
        logger.error("Failed to fetch categories: %s", exc)
        return []


def get_all_categories() -> list[dict]:
    """Return all categories (active and inactive) — for staff use."""
    try:
        client = get_supabase_client()
        response = (
            client.table(CATEGORIES_TABLE)
            .select("category_id, name, slug, description, is_active, created_at, updated_at")
            .order("name")
            .execute()
        )
        return response.data or []
    except APIError as exc:
        logger.error("Failed to fetch all categories: %s", exc)
        return []


def get_category_by_id(category_id: str) -> Optional[dict]:
    """Return a single category by UUID, or None."""
    try:
        client = get_supabase_client()
        response = (
            client.table(CATEGORIES_TABLE)
            .select("*")
            .eq("category_id", category_id)
            .limit(1)
            .execute()
        )
        data = response.data or []
        return data[0] if data else None
    except APIError as exc:
        logger.error("Failed to fetch category %s: %s", category_id, exc)
        return None


def create_category(data: dict) -> dict:
    """Create a new category. Returns the created row."""
    client = get_supabase_client()
    response = client.table(CATEGORIES_TABLE).insert(data).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError("Category creation returned no data.")
    return rows[0]


def update_category(category_id: str, data: dict) -> dict:
    """Update an existing category by UUID. Returns the updated row."""
    client = get_supabase_client()
    response = (
        client.table(CATEGORIES_TABLE)
        .update(data)
        .eq("category_id", category_id)
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise RuntimeError("Category update returned no data.")
    return rows[0]


# ---------------------------------------------------------------------------
# Product queries
# ---------------------------------------------------------------------------


def _build_catalogue_query(client, category_id=None, search=None, in_stock_only=False):
    """
    Build a PostgREST query for the public catalogue.
    Returns only active products in active categories.
    """
    query = (
        client.table(PRODUCTS_TABLE)
        .select(
            "product_id, name, description, weight_value, weight_unit, "
            "serving_count, serving_unit, stock_quantity, price, "
            "discount_type, discount_value, is_active, category_id, "
            "categories!inner(category_id, name, slug, is_active)"
        )
        .eq("is_active", True)
        .eq("categories.is_active", True)
    )

    if category_id:
        query = query.eq("category_id", category_id)

    if search:
        # Safe ILIKE via PostgREST — search argument is passed as a value, not
        # concatenated into the URL in a way that would allow SQL injection
        # through the PostgREST filter parameter.
        query = query.or_(
            f"name.ilike.%{search}%,description.ilike.%{search}%"
        )

    if in_stock_only:
        query = query.gt("stock_quantity", 0)

    return query.order("name")


def get_catalogue_products(
    category_id: Optional[str] = None,
    search: Optional[str] = None,
    in_stock_only: bool = False,
    price_preset: Optional[str] = None,
) -> list[dict]:
    """
    Return enriched, filtered product list for the public catalogue.

    price_preset is one of the PRICE_PRESETS keys. Because PostgREST cannot
    filter on computed effective price, we fetch the constrained active set,
    compute effective price in Python, and filter there.
    This limitation is documented in progress.md.
    """
    try:
        client = get_supabase_client()
        query = _build_catalogue_query(client, category_id, search, in_stock_only)
        response = query.execute()
        products = response.data or []
    except APIError as exc:
        logger.error("Failed to fetch catalogue products: %s", exc)
        return []

    # Enrich with computed price fields
    enriched = [enrich_product(p) for p in products]

    # Apply price-range filter in Python (documented limitation)
    if price_preset:
        preset_map = {key: (lo, hi) for key, _label, lo, hi in PRICE_PRESETS}
        bounds = preset_map.get(price_preset)
        if bounds:
            lo, hi = bounds
            enriched = [
                p for p in enriched
                if p["effective_price"] >= lo and (hi is None or p["effective_price"] <= hi)
            ]

    # Attach images
    if enriched:
        product_ids = [p["product_id"] for p in enriched]
        images_by_product = _fetch_images_for_products(product_ids)
        for p in enriched:
            imgs = images_by_product.get(p["product_id"], [])
            p["images"] = imgs
            p["primary_image"] = pick_primary_image(imgs)

    return enriched


def get_product_by_id(product_id: str, active_only: bool = True) -> Optional[dict]:
    """
    Return a fully enriched single product dict, or None if not found / inactive.
    Raises ValueError for an obviously invalid UUID format.
    """
    try:
        uuid.UUID(product_id)
    except ValueError:
        return None

    try:
        client = get_supabase_client()
        query = (
            client.table(PRODUCTS_TABLE)
            .select(
                "*, categories!inner(category_id, name, slug, is_active)"
            )
            .eq("product_id", product_id)
            .limit(1)
        )
        if active_only:
            query = query.eq("is_active", True).eq("categories.is_active", True)

        response = query.execute()
        data = response.data or []
    except APIError as exc:
        logger.error("Failed to fetch product %s: %s", product_id, exc)
        return None

    if not data:
        return None

    product = enrich_product(data[0])

    # Attach ordered images
    images = _fetch_images_for_product(product_id)
    product["images"] = images
    product["primary_image"] = pick_primary_image(images)

    return product


def get_all_products_staff() -> list[dict]:
    """Return all products (active and inactive) for staff management."""
    try:
        client = get_supabase_client()
        response = (
            client.table(PRODUCTS_TABLE)
            .select("*, categories(category_id, name, slug, is_active)")
            .order("name")
            .execute()
        )
        products = response.data or []
    except APIError as exc:
        logger.error("Failed to fetch staff products: %s", exc)
        return []

    enriched = [enrich_product(p) for p in products]
    if enriched:
        product_ids = [p["product_id"] for p in enriched]
        images_by_product = _fetch_images_for_products(product_ids)
        for p in enriched:
            imgs = images_by_product.get(p["product_id"], [])
            p["images"] = imgs
            p["primary_image"] = pick_primary_image(imgs)
    return enriched


def create_product(data: dict) -> dict:
    """Create a new product. Returns the created row (enriched)."""
    client = get_supabase_client()
    response = client.table(PRODUCTS_TABLE).insert(data).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError("Product creation returned no data.")
    return enrich_product(rows[0])


def update_product(product_id: str, data: dict) -> dict:
    """Update an existing product by UUID. Returns the updated row (enriched)."""
    client = get_supabase_client()
    response = (
        client.table(PRODUCTS_TABLE)
        .update(data)
        .eq("product_id", product_id)
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise RuntimeError("Product update returned no data.")
    return enrich_product(rows[0])


# ---------------------------------------------------------------------------
# Product image queries
# ---------------------------------------------------------------------------


def _fetch_images_for_product(product_id: str) -> list[dict]:
    """Return ordered image rows for a single product."""
    try:
        client = get_supabase_client()
        response = (
            client.table(PRODUCT_IMAGES_TABLE)
            .select("*")
            .eq("product_id", product_id)
            .order("display_order")
            .execute()
        )
        return response.data or []
    except APIError as exc:
        logger.error("Failed to fetch images for product %s: %s", product_id, exc)
        return []


def _fetch_images_for_products(product_ids: list[str]) -> dict[str, list[dict]]:
    """
    Return a mapping of product_id → [images] for multiple products.
    Uses a single query with an IN filter.
    """
    if not product_ids:
        return {}
    try:
        client = get_supabase_client()
        response = (
            client.table(PRODUCT_IMAGES_TABLE)
            .select("*")
            .in_("product_id", product_ids)
            .order("display_order")
            .execute()
        )
        rows = response.data or []
    except APIError as exc:
        logger.error("Failed to batch-fetch product images: %s", exc)
        return {}

    result: dict[str, list[dict]] = {}
    for img in rows:
        pid = img["product_id"]
        result.setdefault(pid, []).append(img)
    return result


def get_images_for_product(product_id: str) -> list[dict]:
    """Public accessor — return ordered images for a product (staff use)."""
    return _fetch_images_for_product(product_id)


def add_product_image(data: dict) -> dict:
    """Create a product_images row. Returns the created row."""
    client = get_supabase_client()
    response = client.table(PRODUCT_IMAGES_TABLE).insert(data).execute()
    rows = response.data or []
    if not rows:
        raise RuntimeError("Image creation returned no data.")
    return rows[0]


def update_product_image(image_id: str, data: dict) -> dict:
    """Update a product_images row. Returns the updated row."""
    client = get_supabase_client()
    response = (
        client.table(PRODUCT_IMAGES_TABLE)
        .update(data)
        .eq("image_id", image_id)
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise RuntimeError("Image update returned no data.")
    return rows[0]


def delete_product_image(image_id: str) -> None:
    """Delete a product_images row by UUID."""
    client = get_supabase_client()
    client.table(PRODUCT_IMAGES_TABLE).delete().eq("image_id", image_id).execute()


def set_primary_image(product_id: str, image_id: str) -> None:
    """
    Set exactly one image as primary for a product.
    Clears is_primary on all other images first to respect the partial unique index.
    """
    client = get_supabase_client()
    # Clear all primaries for this product
    client.table(PRODUCT_IMAGES_TABLE).update({"is_primary": False}).eq(
        "product_id", product_id
    ).execute()
    # Set the chosen one
    client.table(PRODUCT_IMAGES_TABLE).update({"is_primary": True}).eq(
        "image_id", image_id
    ).execute()


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def list_bucket_objects(prefix: str = "") -> list[dict]:
    """
    List objects in the cakes bucket, optionally under a path prefix.
    Returns a list of storage object metadata dicts.
    """
    try:
        client = get_supabase_client()
        options = {"prefix": prefix} if prefix else {}
        result = client.storage.from_(STORAGE_BUCKET).list(prefix or "", options)
        return result or []
    except Exception as exc:
        logger.error("Failed to list bucket objects (prefix=%s): %s", prefix, exc)
        return []


def upload_image_to_storage(storage_path: str, file_bytes: bytes, content_type: str) -> str:
    """
    Upload an image to the cakes bucket at storage_path.
    Returns the storage path on success.
    Raises RuntimeError on failure.
    """
    client = get_supabase_client()
    try:
        client.storage.from_(STORAGE_BUCKET).upload(
            storage_path,
            file_bytes,
            {"content-type": content_type, "upsert": "false"},
        )
        return storage_path
    except Exception as exc:
        logger.error("Storage upload failed for %s: %s", storage_path, exc)
        raise RuntimeError(f"Image upload failed: {exc}") from exc


def delete_storage_object(storage_path: str) -> None:
    """
    Delete an object from the cakes bucket.
    Logs a warning on failure but does not raise — callers must check separately.
    """
    try:
        client = get_supabase_client()
        client.storage.from_(STORAGE_BUCKET).remove([storage_path])
    except Exception as exc:
        logger.warning("Failed to delete storage object %s: %s", storage_path, exc)


def get_public_url(storage_path: str) -> str:
    """Return the public URL for a cakes-bucket object."""
    return get_image_public_url(storage_path)


# ---------------------------------------------------------------------------
# Review helpers (read-only, display only)
# ---------------------------------------------------------------------------


def get_approved_reviews_for_product(product_id: str) -> dict:
    """
    Return approved reviews for a product joined with customer name.
    Returns {'reviews': [...], 'average_rating': Decimal, 'count': int}.
    """
    try:
        client = get_supabase_client()
        response = (
            client.table(REVIEWS_TABLE)
            .select(
                "review_id, rating, message, created_at, "
                "customers(first_name, last_name)"
            )
            .eq("product_id", product_id)
            .eq("is_approved", True)
            .order("created_at", desc=True)
            .execute()
        )
        rows = response.data or []
    except APIError as exc:
        logger.error("Failed to fetch reviews for product %s: %s", product_id, exc)
        return {"reviews": [], "average_rating": None, "count": 0}

    count = len(rows)
    if count > 0:
        avg = Decimal(str(sum(r["rating"] for r in rows))) / Decimal(str(count))
        average_rating = avg.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    else:
        average_rating = None

    return {"reviews": rows, "average_rating": average_rating, "count": count}
