"""
Cart views.

All state-changing actions are POST-only with CSRF protection and PRG redirect.
The cart page is a standard GET.
PayHere checkout is gated behind customer authentication.  Payment confirmation
comes exclusively from PayHere's signed server-to-server notification.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
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
from .stripe_services import CheckoutError
from .payhere_services import create_payhere_checkout, handle_notification
from .delivery_services import delivery_quote, DeliveryError
from customers.services import (
    CustomerError,
    create_address,
    get_address_by_id,
    get_authenticated_customer_id,
    get_customer_addresses,
    get_customer_profile,
    update_address,
)


def _is_customer_authenticated(session) -> bool:
    """Check whether a customer is authenticated in the session."""
    return bool(get_authenticated_customer_id(session))


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


# ── PayHere — authentication-gated hosted payment flow ───────────────────────

def pay_now_placeholder(request):
    """Build a signed PayHere checkout request from server-owned order data."""
    if not _is_customer_authenticated(request.session):
        return redirect("/account/login/?next=/cart/pay/")

    customer_id = get_authenticated_customer_id(request.session)
    addresses = get_customer_addresses(customer_id) if customer_id else []
    customer = get_customer_profile(customer_id) if customer_id else None
    cart = get_cart(request.session)
    default_address = next((item for item in addresses if item.get("is_default")), None)
    billing_address = default_address or (addresses[0] if addresses else {})
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
            contact = {
                "first_name": request.POST.get("first_name", "").strip(),
                "last_name": request.POST.get("last_name", "").strip(),
                "email": request.POST.get("email", "").strip(),
                "phone_number": request.POST.get("phone_number", "").strip(),
            }
            if not contact["first_name"]:
                contact["first_name"] = str((customer or {}).get("first_name") or "").strip()
            if not contact["last_name"]:
                contact["last_name"] = str((customer or {}).get("last_name") or "").strip()
            if not contact["email"]:
                contact["email"] = str((customer or {}).get("email") or "").strip()
            if not contact["phone_number"]:
                contact["phone_number"] = str((customer or {}).get("phone_number") or "").strip()

            delivery_type = request.POST.get("delivery_type", "").strip().upper()
            if delivery_type not in {"PICKUP", "DELIVERY"}:
                raise CheckoutError("Choose pickup or delivery before continuing.")

            address_id = request.POST.get("address_id", "").strip()
            selected_address = (
                get_address_by_id(customer_id, address_id)
                if customer_id and address_id
                else None
            )
            if address_id and not selected_address:
                raise CheckoutError("That saved address could not be found. Please choose another.")

            address = None
            if delivery_type == "DELIVERY":
                address = selected_address
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
                        "recipient_name": request.POST.get("recipient_name", "").strip() or " ".join(
                            part for part in (contact["first_name"], contact["last_name"]) if part
                        ),
                        "phone_number": request.POST.get("address_phone_number", "").strip() or contact["phone_number"] or None,
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

            # PayHere requires billing/contact address fields even for pickup.
            # Delivery selections remain server-verified against the customer's
            # saved addresses; pickup may use a saved address or the form values.
            billing_address = dict(address or selected_address or billing_address or {})
            if not selected_address:
                address_values = {
                    "recipient_name": request.POST.get("recipient_name", "").strip(),
                    "phone_number": request.POST.get("address_phone_number", "").strip() or contact["phone_number"],
                    "address_line_1": request.POST.get("address_line_1", "").strip(),
                    "address_line_2": request.POST.get("address_line_2", "").strip(),
                    "city": request.POST.get("city", "").strip(),
                    "postal_code": request.POST.get("postal_code", "").strip(),
                }
                billing_address.update({
                    key: value for key, value in address_values.items()
                    if value
                })
            if not billing_address.get("recipient_name"):
                billing_address["recipient_name"] = " ".join(
                    part for part in (contact["first_name"], contact["last_name"]) if part
                )
            if not billing_address.get("phone_number"):
                billing_address["phone_number"] = contact["phone_number"]

            checkout_customer = {
                **(customer or {}),
                **contact,
            }
            origin = request.build_absolute_uri("/").rstrip("/")
            checkout = create_payhere_checkout(
                request.session,
                customer=checkout_customer,
                billing_address=billing_address,
                delivery_type=delivery_type,
                delivery_address=address,
                notify_url=getattr(settings, "PAYHERE_NOTIFY_URL", "") or f"{origin}/payments/payhere/notify/",
                return_url=getattr(settings, "PAYHERE_RETURN_URL", "") or f"{origin}/cart/pay/success/",
                cancel_url=getattr(settings, "PAYHERE_CANCEL_URL", "") or f"{origin}/cart/pay/cancel/",
            )
            return render(request, "cart/payhere_checkout.html", {
                "checkout": checkout,
                # ``items`` is a PayHere field, so pass explicit pairs instead
                # of letting the template resolve the dictionary's ``items``.
                "checkout_fields": list(checkout.items()),
            })
        except CheckoutError as exc:
            messages.error(request, str(exc))
        except CustomerError as exc:
            messages.error(request, str(exc))
        except Exception:
            logging.getLogger(__name__).exception("Could not start PayHere checkout")
            messages.error(request, "We couldn't start checkout. Please review your details and try again.")

    return render(request, "cart/pay_now_placeholder.html", {
        "cart": cart,
        "addresses": addresses,
        "delivery_options": delivery_options,
        "customer": customer or {},
        "billing_address": billing_address,
        "checkout_form": {
            "first_name": request.POST.get("first_name", (customer or {}).get("first_name", "")),
            "last_name": request.POST.get("last_name", (customer or {}).get("last_name", "")),
            "email": request.POST.get("email", (customer or {}).get("email", "")),
            "phone_number": request.POST.get("phone_number", (customer or {}).get("phone_number", billing_address.get("phone_number", ""))),
            "address_phone_number": request.POST.get("address_phone_number", billing_address.get("phone_number", "")),
            "recipient_name": request.POST.get("recipient_name", billing_address.get("recipient_name", "")),
            "address_line_1": request.POST.get("address_line_1", billing_address.get("address_line_1", "")),
            "address_line_2": request.POST.get("address_line_2", billing_address.get("address_line_2", "")),
            "city": request.POST.get("city", billing_address.get("city", "")),
            "district": request.POST.get("district", billing_address.get("district", "Colombo")),
            "postal_code": request.POST.get("postal_code", billing_address.get("postal_code", "")),
            "latitude": request.POST.get("latitude", ""),
            "longitude": request.POST.get("longitude", ""),
            "delivery_type": request.POST.get("delivery_type", ""),
            "address_id": request.POST.get("address_id", ""),
        },
        "cart_subtotal": cart.get("subtotal") or "0.00",
        "store_latitude": getattr(settings, "STORE_LATITUDE", "6.9271"),
        "store_longitude": getattr(settings, "STORE_LONGITUDE", "79.8612"),
        "delivery_base_fee": getattr(settings, "DELIVERY_BASE_FEE_LKR", "150.00"),
        "delivery_rate_per_km": getattr(settings, "DELIVERY_RATE_PER_KM_LKR", "80.00"),
        "location_api_base_url": getattr(settings, "LOCATION_API_BASE_URL", ""),
    })


def payment_success(request):
    """Informational return page; only the verified notification confirms payment."""
    return render(request, "cart/payment_result.html", {
        "result": "success",
        "provider": "PayHere",
        "payhere_sandbox": getattr(settings, "PAYHERE_SANDBOX", True),
    })


def payment_cancel(request):
    """Informational cancel page; pending orders remain non-purchases."""
    return render(request, "cart/payment_result.html", {
        "result": "cancel",
        "provider": "PayHere",
        "payhere_sandbox": getattr(settings, "PAYHERE_SANDBOX", True),
    })


@csrf_exempt
def payhere_notify(request):
    """Receive PayHere's signed server notification; browser input cannot confirm payment."""
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        handle_notification(request.POST.dict())
    except CheckoutError as exc:
        return HttpResponse(str(exc), status=400)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("PayHere notification processing failed")
        return HttpResponse("PayHere notification processing failed.", status=500)
    return HttpResponse(status=200)
