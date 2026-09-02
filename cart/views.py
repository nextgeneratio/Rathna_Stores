"""
Cart views.

All state-changing actions are POST-only with CSRF protection and PRG redirect.
The cart page is a standard GET.
PayNow is a placeholder — no payment, order, or stock decrement occurs.
"""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .services import (
    CartError,
    get_cart,
    add_to_cart,
    update_cart_item,
    remove_from_cart,
)


# ── Cart page ────────────────────────────────────────────────────────────────

def cart_detail(request):
    """Display the current session cart."""
    try:
        cart = get_cart(request.session)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Cart page load error: %s", exc)
        cart = {
            "items": [],
            "subtotal": None,
            "subtotal_display": "LKR 0",
            "total_items": 0,
            "total_units": 0,
            "has_purchasable_items": False,
        }
        messages.error(request, "Your cart could not be loaded. Please try again.")

    return render(request, "cart/cart.html", {"cart": cart})


# ── Add to cart ──────────────────────────────────────────────────────────────

@require_POST
def cart_add(request):
    """POST: add a product to the cart."""
    product_id = request.POST.get("product_id", "").strip()
    quantity_raw = request.POST.get("quantity", "1").strip()
    redirect_to = request.POST.get("next", "cart:cart")

    try:
        quantity = int(quantity_raw)
    except (ValueError, TypeError):
        quantity = 1

    try:
        add_to_cart(request.session, product_id, quantity)
        messages.success(request, "Added to your cart.")
    except CartError as exc:
        messages.error(request, str(exc))

    # Honour the `next` parameter only for safe relative paths
    if redirect_to and redirect_to.startswith("/") and not redirect_to.startswith("//"):
        return redirect(redirect_to)
    return redirect("cart:cart")


# ── Update quantity ──────────────────────────────────────────────────────────

@require_POST
def cart_update(request):
    """POST: set a new quantity for a cart item. Quantity 0 removes the item."""
    cart_item_id = request.POST.get("cart_item_id", "").strip()
    quantity_raw = request.POST.get("quantity", "1").strip()

    try:
        quantity = int(quantity_raw)
    except (ValueError, TypeError):
        messages.error(request, "Invalid quantity.")
        return redirect("cart:cart")

    try:
        update_cart_item(request.session, cart_item_id, quantity)
        if quantity == 0:
            messages.success(request, "Item removed from cart.")
        else:
            messages.success(request, "Cart updated.")
    except CartError as exc:
        messages.error(request, str(exc))

    return redirect("cart:cart")


# ── Remove item ──────────────────────────────────────────────────────────────

@require_POST
def cart_remove(request):
    """POST: remove a specific item from the cart."""
    cart_item_id = request.POST.get("cart_item_id", "").strip()

    try:
        remove_from_cart(request.session, cart_item_id)
        messages.success(request, "Item removed from cart.")
    except CartError as exc:
        messages.error(request, str(exc))

    return redirect("cart:cart")


# ── PayNow placeholder ────────────────────────────────────────────────────────

def pay_now_placeholder(request):
    """
    Placeholder view for future payment integration.

    This view does NOT capture card data, call any external payment service,
    create an order row, create a payment row, decrement stock,
    or claim that payment has succeeded.

    It simply informs the visitor that checkout is coming soon.
    """
    return render(request, "cart/pay_now_placeholder.html")
