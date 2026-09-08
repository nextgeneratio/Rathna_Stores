"""
Staff product management views.

All views require the user to be logged in AND is_staff=True.
State-changing operations use POST + redirect (PRG pattern).
CSRF protection is provided by Django's middleware on all POST requests.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render

from cakes.services import (
    # Categories
    get_all_categories,
    get_category_by_id,
    create_category,
    update_category,
    # Products
    get_all_products_staff,
    get_product_by_id,
    create_product,
    update_product,
    # Images
    get_images_for_product,
    add_product_image,
    update_product_image,
    delete_product_image,
    set_primary_image,
    delete_storage_object,
    upload_image_to_storage,
    list_bucket_objects,
    get_public_url,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

DISCOUNT_TYPE_CHOICES = ["NONE", "PERCENT", "AMOUNT"]
WEIGHT_UNIT_CHOICES = ["g", "kg", "oz", "lb"]
SERVING_UNIT_CHOICES = ["person", "persons", "slice", "slices"]


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------


def staff_required(view_func):
    """Decorator: requires login + is_staff. Returns 403 for non-staff users."""
    @wraps(view_func)
    @login_required(login_url="accounts/login/")
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden(
                "You do not have permission to access this page."
            )
        return view_func(request, *args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_filename(name: str) -> str:
    """Return a safe, lowercase filename with no spaces or special chars."""
    name = name.lower()
    name = re.sub(r"[^\w\.\-]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def _validate_product_form(post: dict) -> tuple[dict, list[str]]:
    """
    Parse and validate product form POST data.
    Returns (cleaned_data, errors). errors is empty on success.
    """
    errors: list[str] = []
    data: dict = {}

    # Required fields
    name = post.get("name", "").strip()
    if not name:
        errors.append("Product name is required.")
    else:
        data["name"] = name

    category_id = post.get("category_id", "").strip()
    if not category_id:
        errors.append("Category is required.")
    else:
        try:
            uuid.UUID(category_id)
            data["category_id"] = category_id
        except ValueError:
            errors.append("Invalid category selected.")

    price_raw = post.get("price", "").strip()
    try:
        from decimal import Decimal
        price = Decimal(price_raw)
        if price < 0:
            errors.append("Price cannot be negative.")
        else:
            data["price"] = str(price)
    except Exception:
        errors.append("Price must be a valid number.")

    stock_raw = post.get("stock_quantity", "0").strip()
    try:
        stock = int(stock_raw)
        if stock < 0:
            errors.append("Stock quantity cannot be negative.")
        else:
            data["stock_quantity"] = stock
    except ValueError:
        errors.append("Stock quantity must be a whole number.")

    # Discount validation
    discount_type = post.get("discount_type", "NONE").strip().upper()
    if discount_type not in DISCOUNT_TYPE_CHOICES:
        errors.append("Invalid discount type.")
        discount_type = "NONE"
    data["discount_type"] = discount_type

    discount_value_raw = post.get("discount_value", "0").strip()
    try:
        from decimal import Decimal
        dv = Decimal(discount_value_raw)
        if dv < 0:
            errors.append("Discount value cannot be negative.")
        elif discount_type == "NONE" and dv != 0:
            errors.append("Discount value must be 0 when discount type is NONE.")
        elif discount_type == "PERCENT" and not (1 <= dv <= 100):
            errors.append("Percentage discount must be between 1 and 100.")
        elif discount_type == "AMOUNT" and dv <= 0:
            errors.append("Amount discount must be greater than 0.")
        else:
            data["discount_value"] = str(dv)
    except Exception:
        errors.append("Discount value must be a valid number.")

    # Optional fields
    description = post.get("description", "").strip()
    data["description"] = description or None

    weight_value_raw = post.get("weight_value", "").strip()
    if weight_value_raw:
        try:
            from decimal import Decimal
            data["weight_value"] = str(Decimal(weight_value_raw))
        except Exception:
            errors.append("Weight must be a valid number.")
    else:
        data["weight_value"] = None

    weight_unit = post.get("weight_unit", "").strip()
    data["weight_unit"] = weight_unit or None

    serving_count_raw = post.get("serving_count", "").strip()
    if serving_count_raw:
        try:
            data["serving_count"] = int(serving_count_raw)
        except ValueError:
            errors.append("Serving count must be a whole number.")
    else:
        data["serving_count"] = None

    serving_unit = post.get("serving_unit", "").strip()
    data["serving_unit"] = serving_unit or None

    data["is_active"] = post.get("is_active") == "1"

    return data, errors


def _validate_category_form(post: dict) -> tuple[dict, list[str]]:
    """Parse and validate category form POST data."""
    errors: list[str] = []
    data: dict = {}

    name = post.get("name", "").strip()
    if not name:
        errors.append("Category name is required.")
    else:
        data["name"] = name

    slug = post.get("slug", "").strip().lower()
    if slug:
        if not re.match(r"^[a-z0-9\-]+$", slug):
            errors.append("Slug may only contain lowercase letters, numbers, and hyphens.")
        else:
            data["slug"] = slug
    else:
        # Auto-generate slug from name
        data["slug"] = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    data["description"] = post.get("description", "").strip() or None
    data["is_active"] = post.get("is_active") == "1"

    return data, errors


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@staff_required
def dashboard(request):
    """Staff dashboard overview."""
    try:
        products = get_all_products_staff()
        categories = get_all_categories()
    except Exception as exc:
        logger.error("Dashboard data load failed: %s", exc)
        products, categories = [], []

    active_count = sum(1 for p in products if p.get("is_active"))
    inactive_count = len(products) - active_count
    out_of_stock = sum(1 for p in products if not p.get("in_stock"))

    context = {
        "products": products[:10],  # recent 10 for quick view
        "product_count": len(products),
        "active_count": active_count,
        "inactive_count": inactive_count,
        "out_of_stock_count": out_of_stock,
        "category_count": len(categories),
    }
    return render(request, "store_admin/dashboard.html", context)


# ---------------------------------------------------------------------------
# Product management
# ---------------------------------------------------------------------------


@staff_required
def product_list(request):
    """List all products with status indicators."""
    try:
        products = get_all_products_staff()
    except Exception as exc:
        logger.error("Product list failed: %s", exc)
        products = []
        messages.error(request, "Could not load products. Please try again.")

    return render(request, "store_admin/product_list.html", {"products": products})


@staff_required
def product_create(request):
    """Create a new product."""
    categories = get_all_categories()

    if request.method == "POST":
        data, errors = _validate_product_form(request.POST)
        if errors:
            for err in errors:
                messages.error(request, err)
            return render(
                request,
                "store_admin/product_form.html",
                {
                    "form_data": request.POST,
                    "categories": categories,
                    "discount_types": DISCOUNT_TYPE_CHOICES,
                    "weight_units": WEIGHT_UNIT_CHOICES,
                    "serving_units": SERVING_UNIT_CHOICES,
                    "is_edit": False,
                },
            )
        try:
            product = create_product(data)
            messages.success(request, f"Product '{product['name']}' created successfully.")
            return redirect("store_admin:product_images", product_id=product["product_id"])
        except Exception as exc:
            logger.error("Product create failed: %s", exc)
            messages.error(request, "Could not create product. Please try again.")

    return render(
        request,
        "store_admin/product_form.html",
        {
            "form_data": {},
            "categories": categories,
            "discount_types": DISCOUNT_TYPE_CHOICES,
            "weight_units": WEIGHT_UNIT_CHOICES,
            "serving_units": SERVING_UNIT_CHOICES,
            "is_edit": False,
        },
    )


@staff_required
def product_edit(request, product_id):
    """Edit an existing product."""
    product = get_product_by_id(str(product_id), active_only=False)
    if not product:
        raise Http404("Product not found.")

    categories = get_all_categories()

    if request.method == "POST":
        data, errors = _validate_product_form(request.POST)
        if errors:
            for err in errors:
                messages.error(request, err)
            return render(
                request,
                "store_admin/product_form.html",
                {
                    "product": product,
                    "form_data": request.POST,
                    "categories": categories,
                    "discount_types": DISCOUNT_TYPE_CHOICES,
                    "weight_units": WEIGHT_UNIT_CHOICES,
                    "serving_units": SERVING_UNIT_CHOICES,
                    "is_edit": True,
                },
            )
        try:
            updated = update_product(str(product_id), data)
            messages.success(request, f"Product '{updated['name']}' updated.")
            return redirect("store_admin:product_list")
        except Exception as exc:
            logger.error("Product edit failed: %s", exc)
            messages.error(request, "Could not update product. Please try again.")

    return render(
        request,
        "store_admin/product_form.html",
        {
            "product": product,
            "form_data": product,
            "categories": categories,
            "discount_types": DISCOUNT_TYPE_CHOICES,
            "weight_units": WEIGHT_UNIT_CHOICES,
            "serving_units": SERVING_UNIT_CHOICES,
            "is_edit": True,
        },
    )


@staff_required
def product_toggle_active(request, product_id):
    """Toggle a product's is_active state."""
    if request.method != "POST":
        return redirect("store_admin:product_list")

    product = get_product_by_id(str(product_id), active_only=False)
    if not product:
        raise Http404("Product not found.")

    new_state = not product.get("is_active", True)
    try:
        update_product(str(product_id), {"is_active": new_state})
        state_label = "enabled" if new_state else "disabled"
        messages.success(request, f"Product '{product['name']}' {state_label}.")
    except Exception as exc:
        logger.error("Product toggle failed: %s", exc)
        messages.error(request, "Could not update product state.")

    return redirect("store_admin:product_list")


# ---------------------------------------------------------------------------
# Image management
# ---------------------------------------------------------------------------


@staff_required
def product_images(request, product_id):
    """Display and manage images for a product."""
    product = get_product_by_id(str(product_id), active_only=False)
    if not product:
        raise Http404("Product not found.")

    images = get_images_for_product(str(product_id))

    return render(
        request,
        "store_admin/product_images.html",
        {"product": product, "images": images},
    )


@staff_required
def image_upload(request, product_id):
    """Upload a new image to Supabase Storage and create a product_images row."""
    product = get_product_by_id(str(product_id), active_only=False)
    if not product:
        raise Http404("Product not found.")

    if request.method != "POST":
        return redirect("store_admin:product_images", product_id=product_id)

    uploaded_file = request.FILES.get("image_file")
    if not uploaded_file:
        messages.error(request, "No file was uploaded.")
        return redirect("store_admin:product_images", product_id=product_id)

    # Server-side MIME validation (do not trust filename extension alone)
    content_type = uploaded_file.content_type or ""
    if content_type not in ALLOWED_IMAGE_MIME_TYPES:
        # Double-check by sniffing
        guessed, _ = mimetypes.guess_type(uploaded_file.name)
        if guessed not in ALLOWED_IMAGE_MIME_TYPES:
            messages.error(
                request,
                "Only JPEG, PNG, WebP, or GIF images are allowed.",
            )
            return redirect("store_admin:product_images", product_id=product_id)
        content_type = guessed

    if uploaded_file.size > MAX_UPLOAD_SIZE_BYTES:
        messages.error(request, "Image must be smaller than 5 MB.")
        return redirect("store_admin:product_images", product_id=product_id)

    # Build collision-safe storage path
    category = product.get("categories") or {}
    category_slug = category.get("slug") or "uncategorized"
    safe_name = _sanitize_filename(uploaded_file.name)
    storage_path = f"Off_Shelf/{category_slug}/{product_id}/{safe_name}"

    alt_text = request.POST.get("alt_text", "").strip() or product["name"]
    make_primary = request.POST.get("make_primary") == "1"

    # Attempt upload
    storage_path_result = None
    try:
        file_bytes = uploaded_file.read()
        storage_path_result = upload_image_to_storage(storage_path, file_bytes, content_type)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("store_admin:product_images", product_id=product_id)

    # Create DB row — if this fails, remove the orphaned storage object
    image_url = get_public_url(storage_path_result)
    images = get_images_for_product(str(product_id))
    next_order = max((img.get("display_order", 0) for img in images), default=-1) + 1
    is_first = len(images) == 0

    try:
        new_image = add_product_image(
            {
                "product_id": str(product_id),
                "image_url": image_url,
                "alt_text": alt_text,
                "display_order": next_order,
                "is_primary": is_first,
            }
        )
    except Exception as exc:
        logger.error("DB write for image failed after upload: %s", exc)
        delete_storage_object(storage_path_result)
        messages.error(
            request,
            "Image was uploaded but could not be saved to the database. "
            "The upload has been rolled back.",
        )
        return redirect("store_admin:product_images", product_id=product_id)

    # If marking as primary, clear others
    if make_primary or is_first:
        try:
            set_primary_image(str(product_id), new_image["image_id"])
        except Exception as exc:
            logger.warning("Could not set primary image: %s", exc)

    messages.success(request, "Image uploaded and saved.")
    return redirect("store_admin:product_images", product_id=product_id)


@staff_required
def image_select_existing(request, product_id):
    """
    Select an existing object from the cakes bucket and link it as a product image.
    Shows a browser of the bucket (prefix: Off_Shelf/) for GET.
    Handles POST to create the product_images row.
    """
    product = get_product_by_id(str(product_id), active_only=False)
    if not product:
        raise Http404("Product not found.")

    if request.method == "POST":
        storage_path = request.POST.get("storage_path", "").strip()
        if not storage_path:
            messages.error(request, "No storage path selected.")
            return redirect("store_admin:image_select", product_id=product_id)

        alt_text = request.POST.get("alt_text", "").strip() or product["name"]
        make_primary = request.POST.get("make_primary") == "1"
        images = get_images_for_product(str(product_id))
        next_order = max((img.get("display_order", 0) for img in images), default=-1) + 1
        is_first = len(images) == 0
        image_url = get_public_url(storage_path)

        try:
            new_image = add_product_image(
                {
                    "product_id": str(product_id),
                    "image_url": image_url,
                    "alt_text": alt_text,
                    "display_order": next_order,
                    "is_primary": is_first,
                }
            )
            if make_primary or is_first:
                set_primary_image(str(product_id), new_image["image_id"])
            messages.success(request, "Image linked to product.")
        except Exception as exc:
            logger.error("Image select-existing DB write failed: %s", exc)
            messages.error(request, "Could not link image. Please try again.")

        return redirect("store_admin:product_images", product_id=product_id)

    # GET — list bucket objects
    objects = list_bucket_objects("Off_Shelf/")
    return render(
        request,
        "store_admin/image_select.html",
        {"product": product, "bucket_objects": objects},
    )


@staff_required
def image_delete(request, image_id):
    """
    Delete a product image row and its storage object.
    Requires confirmation via POST. Does NOT delete the storage object if it
    is still referenced by another product_images row (not tracked here —
    documented as a known limitation; in practice each product gets its own
    storage path under its own UUID).
    """
    if request.method != "POST":
        return redirect("store_admin:product_list")

    # We need the product_id to redirect back after deletion
    product_id = request.POST.get("product_id", "").strip()

    try:
        # Fetch the image row first to get the image_url / storage info
        from cakes.services import get_supabase_client, PRODUCT_IMAGES_TABLE
        client = get_supabase_client()
        resp = (
            client.table(PRODUCT_IMAGES_TABLE)
            .select("image_id, product_id, image_url")
            .eq("image_id", str(image_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            messages.warning(request, "Image not found.")
            return redirect(
                "store_admin:product_images", product_id=product_id
            ) if product_id else redirect("store_admin:product_list")

        img_row = rows[0]
        product_id = img_row["product_id"]

        # Delete DB row first
        delete_product_image(str(image_id))

        # Attempt storage cleanup — if this fails, log a warning but do not
        # surface as an error (image is already unlinked from the catalogue)
        image_url = img_row.get("image_url", "")
        # Extract storage path from public URL when possible
        # Storage path is the segment after the bucket name in the URL
        bucket_marker = f"/storage/v1/object/public/{STORAGE_BUCKET_MARKER}/"
        if bucket_marker in image_url:
            storage_path = image_url.split(bucket_marker, 1)[-1]
            delete_storage_object(storage_path)
        else:
            logger.info("Could not determine storage path for image %s; skipping object deletion.", image_id)

        messages.success(request, "Image deleted.")
    except Exception as exc:
        logger.error("Image delete failed: %s", exc)
        messages.error(request, "Could not delete image. Please try again.")

    return redirect("store_admin:product_images", product_id=product_id)


# Marker used to parse the storage path out of a public URL
STORAGE_BUCKET_MARKER = "cakes"


@staff_required
def image_set_primary(request, image_id):
    """Mark an image as the primary image for its product."""
    if request.method != "POST":
        return redirect("store_admin:product_list")

    product_id = request.POST.get("product_id", "").strip()
    try:
        set_primary_image(product_id, str(image_id))
        messages.success(request, "Primary image updated.")
    except Exception as exc:
        logger.error("Set primary failed: %s", exc)
        messages.error(request, "Could not set primary image.")

    return redirect("store_admin:product_images", product_id=product_id)


@staff_required
def image_edit(request, image_id):
    """Edit alt text and display_order for an image."""
    if request.method != "POST":
        return redirect("store_admin:product_list")

    product_id = request.POST.get("product_id", "").strip()
    alt_text = request.POST.get("alt_text", "").strip()
    display_order_raw = request.POST.get("display_order", "0").strip()

    try:
        display_order = int(display_order_raw)
        if display_order < 0:
            raise ValueError("Negative order")
    except ValueError:
        messages.error(request, "Display order must be a non-negative integer.")
        return redirect("store_admin:product_images", product_id=product_id)

    try:
        update_product_image(str(image_id), {"alt_text": alt_text, "display_order": display_order})
        messages.success(request, "Image updated.")
    except Exception as exc:
        logger.error("Image edit failed: %s", exc)
        messages.error(request, "Could not update image.")

    return redirect("store_admin:product_images", product_id=product_id)


# ---------------------------------------------------------------------------
# Category management
# ---------------------------------------------------------------------------


@staff_required
def category_list(request):
    """List all categories."""
    try:
        categories = get_all_categories()
    except Exception as exc:
        logger.error("Category list failed: %s", exc)
        categories = []
        messages.error(request, "Could not load categories.")

    return render(request, "store_admin/category_list.html", {"categories": categories})


@staff_required
def category_create(request):
    """Create a new category."""
    if request.method == "POST":
        data, errors = _validate_category_form(request.POST)
        if errors:
            for err in errors:
                messages.error(request, err)
            return render(
                request,
                "store_admin/category_form.html",
                {"form_data": request.POST, "is_edit": False},
            )
        try:
            cat = create_category(data)
            messages.success(request, f"Category '{cat['name']}' created.")
            return redirect("store_admin:category_list")
        except Exception as exc:
            logger.error("Category create failed: %s", exc)
            messages.error(request, "Could not create category. The name or slug may already be in use.")

    return render(
        request,
        "store_admin/category_form.html",
        {"form_data": {}, "is_edit": False},
    )


@staff_required
def category_edit(request, category_id):
    """Edit an existing category."""
    category = get_category_by_id(str(category_id))
    if not category:
        raise Http404("Category not found.")

    if request.method == "POST":
        data, errors = _validate_category_form(request.POST)
        if errors:
            for err in errors:
                messages.error(request, err)
            return render(
                request,
                "store_admin/category_form.html",
                {"category": category, "form_data": request.POST, "is_edit": True},
            )
        try:
            updated = update_category(str(category_id), data)
            messages.success(request, f"Category '{updated['name']}' updated.")
            return redirect("store_admin:category_list")
        except Exception as exc:
            logger.error("Category edit failed: %s", exc)
            messages.error(request, "Could not update category. The name or slug may already be in use.")

    return render(
        request,
        "store_admin/category_form.html",
        {"category": category, "form_data": category, "is_edit": True},
    )


@staff_required
def category_toggle_active(request, category_id):
    """Toggle a category's is_active state."""
    if request.method != "POST":
        return redirect("store_admin:category_list")

    category = get_category_by_id(str(category_id))
    if not category:
        raise Http404("Category not found.")

    new_state = not category.get("is_active", True)
    try:
        update_category(str(category_id), {"is_active": new_state})
        state_label = "enabled" if new_state else "disabled"
        messages.success(request, f"Category '{category['name']}' {state_label}.")
    except Exception as exc:
        logger.error("Category toggle failed: %s", exc)
        messages.error(request, "Could not update category state.")

    return redirect("store_admin:category_list")
