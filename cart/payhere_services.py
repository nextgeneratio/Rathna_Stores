"""PayHere Checkout form creation and verified notification handling.

PayHere Checkout is a browser POST to their hosted payment page.  The secret
hash is deliberately calculated here, never in JavaScript.  A customer return
to the site is informational only; payment state changes only after PayHere
posts a notification whose ``md5sig`` has been verified.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from Rathna_Stores.supabase_client import get_supabase_client, get_supabase_service_client
from .services import CARTS_TABLE, CART_ITEMS_TABLE
from .stripe_services import (
    ORDERS_TABLE,
    PAYMENTS_TABLE,
    CheckoutError,
    _create_pending_order,
    _money,
)

logger = logging.getLogger(__name__)


def _md5_upper(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest().upper()


def _configured_credentials() -> tuple[str, str]:
    merchant_id = str(getattr(settings, "PAYHERE_MERCHANT_ID", "") or "").strip()
    merchant_secret = str(getattr(settings, "PAYHERE_MERCHANT_SECRET", "") or "").strip()
    if not merchant_id or not merchant_secret:
        raise CheckoutError("PayHere checkout is not configured yet.")
    return merchant_id, merchant_secret


def _payment_finalizer_client():
    """Return the privileged client required by the verified callback.

    The notification is deliberately finalized with the Supabase service-role
    client: the RPC changes payment/order state, stock, and the customer's cart
    in one transaction and is not callable by browsers.  Check its
    configuration *before* an order is created, so a merchant can never send a
    customer to PayHere when this installation could not finish the order.
    """
    try:
        return get_supabase_service_client()
    except ValueError as exc:
        raise CheckoutError(
            "PayHere checkout is temporarily unavailable because automatic "
            "payment confirmation is not configured. Please contact the store."
        ) from exc


def _amount_text(value: Any) -> str:
    """Return the precise two-decimal amount PayHere expects in its hash."""
    return format(_money(value), ".2f")


def _request_hash(merchant_id: str, order_id: str, amount: str, currency: str, merchant_secret: str) -> str:
    return _md5_upper(
        f"{merchant_id}{order_id}{amount}{currency}{_md5_upper(merchant_secret)}"
    )


def _notification_hash(
    merchant_id: str,
    order_id: str,
    amount: str,
    currency: str,
    status_code: str,
    merchant_secret: str,
) -> str:
    return _md5_upper(
        f"{merchant_id}{order_id}{amount}{currency}{status_code}{_md5_upper(merchant_secret)}"
    )


def checkout_action_url() -> str:
    """Return the correct hosted Checkout endpoint for the active environment."""
    if getattr(settings, "PAYHERE_SANDBOX", True):
        return "https://sandbox.payhere.lk/pay/checkout"
    return "https://www.payhere.lk/pay/checkout"


def _validate_notify_url(notify_url: str) -> str:
    """Reject callback URLs that PayHere cannot call from the public internet."""
    value = str(notify_url or "").strip()
    parsed = urlparse(value)
    private_hosts = {"localhost", "127.0.0.1", "::1", "[::1]"}
    if parsed.scheme != "https" or not parsed.netloc or parsed.hostname in private_hosts:
        raise CheckoutError(
            "PayHere requires a public HTTPS notification URL. Configure "
            "PAYHERE_NOTIFY_URL with your deployed or tunnel domain, not localhost."
        )
    return value


def _customer_details(customer: dict | None, billing_address: dict | None) -> dict[str, str]:
    customer = customer or {}
    billing_address = billing_address or {}
    first_name = str(customer.get("first_name") or billing_address.get("recipient_name") or "").strip()
    last_name = str(customer.get("last_name") or "").strip()
    email = str(customer.get("email") or "").strip()
    phone = str(customer.get("phone_number") or billing_address.get("phone_number") or "").strip()
    address = " ".join(
        str(billing_address.get(key) or "").strip()
        for key in ("address_line_1", "address_line_2")
        if str(billing_address.get(key) or "").strip()
    )
    city = str(billing_address.get("city") or "").strip()
    if not all((first_name, last_name, email, phone, address, city)):
        raise CheckoutError(
            "Enter your full name, a valid email, phone number, and address before continuing to PayHere."
        )
    try:
        validate_email(email)
    except ValidationError as exc:
        raise CheckoutError("Enter a valid email address before continuing to PayHere.") from exc
    return {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "address": address,
        "city": city,
        "country": "Sri Lanka",
    }


def create_payhere_checkout(
    session,
    *,
    customer: dict | None,
    billing_address: dict | None,
    notify_url: str,
    return_url: str,
    cancel_url: str,
    delivery_type: str = "PICKUP",
    delivery_address: dict | None = None,
) -> dict[str, str]:
    """Create a pending order and return a signed PayHere Checkout form payload."""
    merchant_id, merchant_secret = _configured_credentials()
    notify_url = _validate_notify_url(notify_url)
    details = _customer_details(customer, billing_address)
    _payment_finalizer_client()
    order, checkout, _cart = _create_pending_order(
        session, delivery_type, delivery_address, require_distance=True
    )
    order_id = str(order["order_id"])
    amount = _amount_text(checkout["total"])
    client = get_supabase_client()
    try:
        client.table(PAYMENTS_TABLE).insert({
            "order_id": order_id,
            "payment_reference": f"payhere:{order_id}",
            "amount": amount,
            "provider": "payhere",
            # PayHere returns our order_id in its notification, so it is the
            # provider checkout identifier until PayHere issues payment_id.
            "provider_checkout_session_id": order_id,
            "idempotency_key": f"payhere:{order_id}",
            "payment_method": "payhere",
            "payment_status": "PENDING",
        }).execute()
        client.table(ORDERS_TABLE).update({
            "payment_method": "payhere",
            "payment_status": "PENDING",
        }).eq("order_id", order_id).execute()
    except Exception as exc:
        logger.exception("Could not create PayHere payment for order %s", order_id)
        client.table(ORDERS_TABLE).update({
            "order_status": "CANCELLED", "payment_status": "FAILED"
        }).eq("order_id", order_id).execute()
        raise CheckoutError("Could not start PayHere checkout. No payment was taken.") from exc

    payload = {
        "merchant_id": merchant_id,
        "return_url": return_url,
        "cancel_url": cancel_url,
        "notify_url": notify_url,
        "order_id": order_id,
        "items": f"Rathna Stores order {order.get('order_number', order_id)}",
        "currency": "LKR",
        "amount": amount,
        "hash": _request_hash(merchant_id, order_id, amount, "LKR", merchant_secret),
        "custom_1": str(order.get("order_number", "")),
        **details,
    }
    if delivery_address:
        payload.update({
            "delivery_address": " ".join(
                str(delivery_address.get(key) or "").strip()
                for key in ("address_line_1", "address_line_2")
                if str(delivery_address.get(key) or "").strip()
            ),
            "delivery_city": str(delivery_address.get("city") or "").strip(),
            "delivery_country": "Sri Lanka",
        })
    return {**payload, "action": checkout_action_url()}


def _payment_for_order(client, order_id: str) -> dict | None:
    rows = (
        client.table(PAYMENTS_TABLE)
        .select("order_id,amount,payment_status")
        .eq("provider", "payhere")
        .eq("provider_checkout_session_id", order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def _clear_paid_order_items_from_cart(client, order_id: str) -> None:
    """Remove only products in the confirmed order from its customer's cart."""
    order_rows = (
        client.table(ORDERS_TABLE)
        .select("customer_id")
        .eq("order_id", order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not order_rows:
        raise CheckoutError("Confirmed PayHere order was not found while clearing its cart.")

    order_items = (
        client.table("order_items")
        .select("product_id")
        .eq("order_id", order_id)
        .execute()
        .data
        or []
    )
    product_ids = list(dict.fromkeys(
        item["product_id"] for item in order_items if item.get("product_id")
    ))
    if not product_ids:
        return

    carts = (
        client.table(CARTS_TABLE)
        .select("cart_id")
        .eq("customer_id", order_rows[0]["customer_id"])
        .execute()
        .data
        or []
    )
    for cart in carts:
        client.table(CART_ITEMS_TABLE).delete().eq("cart_id", cart["cart_id"]).in_(
            "product_id", product_ids
        ).execute()


def handle_notification(data: dict[str, Any]) -> None:
    """Verify and process PayHere's server-to-server notification POST.

    Only status code ``2`` can finalize an order.  The Supabase function named
    below locks the order and stock rows, making duplicate notification retries
    safe.
    """
    merchant_id, merchant_secret = _configured_credentials()
    order_id = str(data.get("order_id") or "").strip()
    payment_id = str(data.get("payment_id") or "").strip()
    amount = str(data.get("payhere_amount") or "").strip()
    currency = str(data.get("payhere_currency") or "").strip().upper()
    status_code = str(data.get("status_code") or "").strip()
    received_signature = str(data.get("md5sig") or "").strip().upper()
    if not all((order_id, payment_id, amount, currency, status_code, received_signature)):
        raise CheckoutError("PayHere notification is missing required fields.")
    if str(data.get("merchant_id") or "").strip() != merchant_id:
        raise CheckoutError("PayHere notification merchant does not match this site.")
    expected_signature = _notification_hash(
        merchant_id, order_id, amount, currency, status_code, merchant_secret
    )
    if not hmac.compare_digest(expected_signature, received_signature):
        raise CheckoutError("Invalid PayHere notification signature.")
    if currency != "LKR":
        raise CheckoutError("Unexpected PayHere payment currency.")

    client = _payment_finalizer_client()
    payment = _payment_for_order(client, order_id)
    if not payment:
        raise CheckoutError("PayHere notification refers to an unknown order.")
    try:
        if _money(amount) != _money(payment.get("amount")):
            raise CheckoutError("PayHere notification amount does not match the order.")
    except (InvalidOperation, ValueError) as exc:
        raise CheckoutError("PayHere notification has an invalid amount.") from exc

    if status_code == "2":
        client.rpc("finalize_payhere_payment", {
            "p_event_id": f"{payment_id}:2",
            "p_order_id": order_id,
            "p_payment_id": payment_id,
            "p_amount": _amount_text(amount),
        }).execute()
        _clear_paid_order_items_from_cart(client, order_id)
    elif status_code in {"-1", "-2", "-3"}:
        client.table(PAYMENTS_TABLE).update({
            "payment_status": "FAILED", "transaction_id": payment_id,
        }).eq("provider", "payhere").eq("provider_checkout_session_id", order_id).eq(
            "payment_status", "PENDING"
        ).execute()
        client.table(ORDERS_TABLE).update({
            "order_status": "CANCELLED", "payment_status": "FAILED"
        }).eq("order_id", order_id).eq("payment_status", "PENDING").execute()
