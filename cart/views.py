"""
Cart views.

All state-changing actions are POST-only with CSRF protection and PRG redirect.
The cart page is a standard GET.
PayNow is gated behind customer authentication and remains a non-payment placeholder.
"""
from __future__ import annotations

from django.contrib import messages
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import (
    CartError,
    get_cart,
    add_to_cart,
    update_cart_item,
    remove_from_cart,
)
from .stripe_services import CheckoutError, create_checkout_session, handle_webhook


def _is_customer_authenticated(session) -> bool:
    """Check whether a customer is authenticated in the session."""
    try:
        from customers.services import CUSTOMER_ID_SESSION_KEY
        return bool(session.get(CUSTOMER_ID_SESSION_KEY))
    except Exception:
        return False


# ── Cart page ─────────────────────────────────────────────────────────────────

def cart_detail(request):
    """Display the current session/customer cart."""
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


# ── Add to cart ───────────────────────────────────────────────────────────────

@require_POST
def cart_add(request):
    """POST: add a product to the cart. Works for both guest and authenticated users."""
    product_id = request.POST.get("product_id", "").strip()
    quantity_raw = request.POST.get("quantity", "1").strip()
    redirect_to = request.POST.get("next", "")

    try:
        quantity = int(quantity_raw)
    except (ValueError, TypeError):
        quantity = 1

    try:
        add_to_cart(request.session, product_id, quantity)
        messages.success(request, "Added to your cart.")
    except CartError as exc:
        messages.error(request, str(exc))

    if redirect_to and redirect_to.startswith("/") and not redirect_to.startswith("//"):
        return redirect(redirect_to)
    return redirect("cart:cart")


# ── Update quantity ───────────────────────────────────────────────────────────

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


# ── Remove item ───────────────────────────────────────────────────────────────

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


# ── PayNow — authentication gated placeholder ─────────────────────────────────

def pay_now_placeholder(request):
    """
    Show or start the Stripe test-mode checkout.
    """
    if not _is_customer_authenticated(request.session):
        return redirect("/account/login/?next=/cart/pay/")

    if request.method == "POST":
        try:
            origin = request.build_absolute_uri("/").rstrip("/")
            checkout_url = create_checkout_session(request.session, origin)
            return redirect(checkout_url)
        except CheckoutError as exc:
            messages.error(request, str(exc))

    return render(request, "cart/pay_now_placeholder.html", {
        "stripe_test_configured": bool(getattr(settings, "STRIPE_SECRET_KEY", "").startswith("sk_test_")),
    })


def payment_success(request):
    """Informational return page; only the webhook can confirm payment."""
    return render(request, "cart/payment_result.html", {"result": "success"})


def payment_cancel(request):
    """Informational cancel page; pending orders remain non-purchases."""
    return render(request, "cart/payment_result.html", {"result": "cancel"})


@csrf_exempt
def stripe_webhook(request):
    """Receive Stripe's signed webhook without exposing a browser mutation path."""
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        handle_webhook(request.body, request.headers.get("Stripe-Signature", ""))
    except CheckoutError as exc:
        return HttpResponse(str(exc), status=400)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Stripe webhook processing failed")
        return HttpResponse("Webhook processing failed.", status=500)
    return HttpResponse(status=200)
