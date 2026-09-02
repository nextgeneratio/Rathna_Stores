"""Public views: home/catalogue and product detail."""
from django.shortcuts import render
from django.http import Http404

from .services import (
    get_active_categories,
    get_catalogue_products,
    get_product_by_id,
    get_approved_reviews_for_product,
    PRICE_PRESETS,
)


def catalogue(request):
    """
    Root page — public cake catalogue with product showcase hero.
    Supports GET params: q (search), category, price, in_stock.
    """
    search = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip() or None
    price_preset = request.GET.get("price", "").strip() or None
    in_stock_only = request.GET.get("in_stock") == "1"

    categories = get_active_categories()

    products = get_catalogue_products(
        category_id=category_id,
        search=search,
        in_stock_only=in_stock_only,
        price_preset=price_preset,
    )

    # Resolve active category name for breadcrumb/heading
    active_category = None
    if category_id:
        for cat in categories:
            if cat["category_id"] == category_id:
                active_category = cat
                break

    # Build price preset label for display
    active_price_label = None
    price_presets_display = [
        {"key": key, "label": label}
        for key, label, _lo, _hi in PRICE_PRESETS
    ]
    if price_preset:
        for p in price_presets_display:
            if p["key"] == price_preset:
                active_price_label = p["label"]
                break

    has_filters = bool(search or category_id or price_preset or in_stock_only)

    # Showcase products for hero (only on unfiltered home view)
    showcase_products = []
    if not has_filters:
        try:
            from cart.services import get_showcase_products
            showcase_products = get_showcase_products(limit=5)
        except Exception:
            showcase_products = []

    context = {
        "products": products,
        "categories": categories,
        "price_presets": price_presets_display,
        "search": search,
        "selected_category": category_id,
        "selected_price": price_preset,
        "in_stock_only": in_stock_only,
        "active_category": active_category,
        "active_price_label": active_price_label,
        "has_filters": has_filters,
        "product_count": len(products),
        "showcase_products": showcase_products,
    }
    return render(request, "cakes/catalogue.html", context)


def product_detail(request, product_id):
    """
    Product detail page. Returns real 404 for missing/inactive products.
    product_id is a UUID validated by Django's <uuid:> converter.
    """
    product = get_product_by_id(str(product_id), active_only=True)
    if not product:
        raise Http404("Product not found.")

    review_data = get_approved_reviews_for_product(str(product_id))

    # Category for breadcrumb
    category = product.get("categories") or {}

    context = {
        "product": product,
        "category": category,
        "reviews": review_data["reviews"],
        "review_count": review_data["count"],
        "average_rating": review_data["average_rating"],
    }
    return render(request, "cakes/product_detail.html", context)
