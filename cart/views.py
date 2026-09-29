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
from .stripe_services import CheckoutError, create_checkout_session, create_simulated_order, handle_webhook
from .payment_services import create_offline_payment_order
from .delivery_services import delivery_quote, DeliveryError
from customers.services import create_address, get_address_by_id, get_customer_addresses, get_authenticated_customer_id, update_address


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

    # Let the navigation context processor reuse this freshly calculated
    # total instead of making an additional cart-count request while rendering.
    request._rathna_cart_unit_count = cart["total_units"]
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


# ── PayNow — authentication-gated offline payment flow ───────────────────────

def pay_now_placeholder(request):
    """Create an offline payment request; Stripe remains available internally."""
    if not _is_customer_authenticated(request.session):
        return redirect("/account/login/?next=/cart/pay/")

    customer_id = get_authenticated_customer_id(request.session)
    addresses = get_customer_addresses(customer_id) if customer_id else []
    cart = get_cart(request.session)
    delivery_options = []
    for saved_address in addresses:
        option = dict(saved_address)
        try:
            quote = delivery_quote(saved_address)
            option.update({"delivery_fee": quote["fee"], "distance_km": quote["distance_km"]})
        except DeliveryError:
            option.update({"delivery_fee": None, "distance_km": None})
        delivery_options.append(option)

    if request.method == "POST":
        try:
            delivery_type = request.POST.get("delivery_type", "").strip().upper()
            address = None
            if delivery_type == "DELIVERY":
                address_id = request.POST.get("address_id", "").strip()
                address = get_address_by_id(customer_id, address_id) if customer_id else None
                if address and request.POST.get("latitude", "").strip() and request.POST.get("longitude", "").strip():
                    address = update_address(customer_id, address_id, {
                        "latitude": request.POST.get("latitude", "").strip(),
                        "longitude": request.POST.get("longitude", "").strip(),
                    })
                if not address and request.POST.get("address_line_1", "").strip():
                    manual_district = request.POST.get("district", "").strip()
                    if manual_district.lower() != "colombo":
                        raise CheckoutError("Delivery is available only in Colombo district.")
                    address = create_address(customer_id, {
                        "recipient_name": request.POST.get("recipient_name", "").strip(),
                        "phone_number": request.POST.get("phone_number", "").strip() or None,
                        "address_line_1": request.POST.get("address_line_1", "").strip(),
                        "address_line_2": request.POST.get("address_line_2", "").strip() or None,
                        "city": request.POST.get("city", "").strip(),
                        "district": manual_district,
                        "postal_code": request.POST.get("postal_code", "").strip() or None,
                        "latitude": request.POST.get("latitude", "").strip() or None,
                        "longitude": request.POST.get("longitude", "").strip() or None,
                        "is_default": False,
                    })
                if not address or str(address.get("district", "")).strip().lower() != "colombo":
                    raise CheckoutError("Delivery is available only for an address in Colombo district.")
            order = create_offline_payment_order(
                request.session,
                delivery_type,
                address,
                request.POST.get("payment_method", "BANK_TRANSFER"),
                request.POST.get("payment_reference", ""),
            )
            return render(request, "cart/offline_payment_pending.html", {"order": order})
        except CheckoutError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, str(exc))

    return render(request, "cart/pay_now_placeholder.html", {
        "addresses": addresses,
        "delivery_options": delivery_options,
        "cart_subtotal": cart.get("subtotal") or "0.00",
        "store_latitude": getattr(settings, "STORE_LATITUDE", "6.9271"),
        "store_longitude": getattr(settings, "STORE_LONGITUDE", "79.8612"),
        "delivery_base_fee": getattr(settings, "DELIVERY_BASE_FEE_LKR", "150.00"),
        "delivery_rate_per_km": getattr(settings, "DELIVERY_RATE_PER_KM_LKR", "80.00"),
        "location_api_base_url": getattr(settings, "LOCATION_API_BASE_URL", ""),
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
