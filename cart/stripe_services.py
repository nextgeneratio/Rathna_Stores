"""Stripe test-mode checkout and verified webhook processing."""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import stripe
from django.conf import settings
from django.utils import timezone

from Rathna_Stores.supabase_client import get_supabase_client
from .services import CART_ITEMS_TABLE, CARTS_TABLE, get_cart

logger = logging.getLogger(__name__)

ORDERS_TABLE = "orders"
ORDER_ITEMS_TABLE = "order_items"
PAYMENTS_TABLE = "payments"
PRODUCTS_TABLE = "products"
WEBHOOK_EVENTS_TABLE = "stripe_webhook_events"


class CheckoutError(Exception):
    """A safe checkout failure suitable for a customer-facing message."""


def _configure_stripe() -> None:
    secret = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
    if not getattr(settings, "STRIPE_TEST_MODE", True) or not secret.startswith("sk_test_"):
        raise CheckoutError("Stripe test checkout is not configured yet.")
    stripe.api_key = secret


def _new_order_number() -> str:
    return "RS-" + uuid.uuid4().hex[:12].upper()


def _money(value: Any) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _cart_for_checkout(session) -> tuple[dict, str]:
    from customers.services import CUSTOMER_ID_SESSION_KEY

    customer_id = session.get(CUSTOMER_ID_SESSION_KEY)
    if not customer_id:
        raise CheckoutError("Please sign in before starting checkout.")
    cart = get_cart(session)
    items = [item for item in cart.get("items", []) if item.get("is_available") and int(item.get("quantity", 0)) > 0]
    if not items or not cart.get("has_purchasable_items"):
        raise CheckoutError("Your cart has no available items to check out.")
    return {**cart, "items": items}, str(customer_id)


def _create_pending_order(session) -> tuple[dict, dict, dict]:
    cart, customer_id = _cart_for_checkout(session)
    client = get_supabase_client()
    order_id = str(uuid.uuid4())
    total = _money(cart.get("subtotal") or "0")
    order = {
        "order_id": order_id,
        "customer_id": customer_id,
        "order_number": _new_order_number(),
        "delivery_type": "PICKUP",
        "subtotal": str(total),
        "discount_amount": "0.00",
        "delivery_fee": "0.00",
        "total_amount": str(total),
        "order_status": "PENDING",
        "payment_status": "PENDING",
        "payment_method": "stripe_test",
        "order_date": timezone.now().isoformat(),
    }
    created = client.table(ORDERS_TABLE).insert(order).execute().data or []
    if not created:
        raise CheckoutError("Could not start the checkout. Please try again.")

    order_items = []
    line_items = []
    for item in cart["items"]:
        quantity = int(item["effective_qty"])
        unit_price = _money(item.get("unit_price") or item.get("effective_price") or "0")
        line_total = _money(unit_price * quantity)
        product_name = (item.get("product") or {}).get("name", "Cake")
        order_items.append({
            "order_id": order_id,
            "product_id": item["product_id"],
            "product_name_snapshot": product_name,
            "quantity": quantity,
            "unit_price": str(unit_price),
            "discount_amount": "0.00",
            "item_total": str(line_total),
        })
        line_items.append({
            "price_data": {
                "currency": "lkr",
                "product_data": {"name": product_name},
                "unit_amount": int(unit_price * 100),
            },
            "quantity": quantity,
        })
    client.table(ORDER_ITEMS_TABLE).insert(order_items).execute()
    return created[0], {"line_items": line_items, "total": total}, cart


def create_checkout_session(session, origin: str) -> str:
    """Create a Stripe-hosted test Checkout Session from a fresh server cart read."""
    _configure_stripe()
    order, checkout, _cart = _create_pending_order(session)
    order_id = str(order["order_id"])
    idempotency_key = f"checkout:{order_id}"
    client = get_supabase_client()
    try:
        stripe_session = stripe.checkout.Session.create(
            mode="payment",
            line_items=checkout["line_items"],
            success_url=f"{origin}/cart/pay/success/?order={order_id}",
            cancel_url=f"{origin}/cart/pay/cancel/?order={order_id}",
            client_reference_id=order_id,
            metadata={"order_id": order_id},
            idempotency_key=idempotency_key,
        )
        session_id = str(stripe_session["id"])
        client.table(PAYMENTS_TABLE).insert({
            "order_id": order_id,
            "payment_reference": session_id,
            "amount": str(checkout["total"]),
            "provider": "stripe",
            "provider_checkout_session_id": session_id,
            "idempotency_key": idempotency_key,
            "payment_method": "stripe_test",
            "payment_status": "PENDING",
            "transaction_id": session_id,
        }).execute()
        client.table(ORDERS_TABLE).update({"payment_method": "stripe", "payment_status": "PENDING"}).eq("order_id", order_id).execute()
        return str(stripe_session["url"])
    except Exception as exc:
        logger.error("Stripe test Checkout Session creation failed for order %s: %s", order_id, exc)
        client.table(ORDERS_TABLE).update({"order_status": "CANCELLED", "payment_status": "FAILED"}).eq("order_id", order_id).execute()
        raise CheckoutError("The test checkout could not be started. No payment was taken.") from exc


def _claim_event(client, event_id: str, event_type: str) -> bool:
    try:
        client.table(WEBHOOK_EVENTS_TABLE).insert({"event_id": event_id, "event_type": event_type, "processing_status": "FAILED"}).execute()
        return True
    except Exception as exc:
        # Failed claims are retryable; successfully processed events are done.
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            existing = client.table(WEBHOOK_EVENTS_TABLE).select("processing_status").eq("event_id", event_id).limit(1).execute().data or []
            return bool(existing and existing[0].get("processing_status") == "FAILED")
        raise


def _finalize_paid_order(client, order_id: str, customer_id: str) -> None:
    """Apply one-time post-payment effects from server-owned order snapshots."""
    items = client.table(ORDER_ITEMS_TABLE).select("product_id,quantity").eq("order_id", order_id).execute().data or []
    for item in items:
        product_rows = client.table(PRODUCTS_TABLE).select("stock_quantity").eq("product_id", item["product_id"]).limit(1).execute().data or []
        if not product_rows:
            raise CheckoutError("The paid order references a missing product.")
        current_stock = int(product_rows[0].get("stock_quantity") or 0)
        quantity = int(item.get("quantity") or 0)
        if current_stock < quantity:
            raise CheckoutError("Stock changed before payment confirmation.")
        client.table(PRODUCTS_TABLE).update({"stock_quantity": current_stock - quantity}).eq("product_id", item["product_id"]).execute()

    carts = client.table(CARTS_TABLE).select("cart_id").eq("customer_id", customer_id).limit(1).execute().data or []
    if carts:
        client.table(CART_ITEMS_TABLE).delete().eq("cart_id", carts[0]["cart_id"]).execute()


def _mark_paid(event: dict) -> None:
    session = event.get("data", {}).get("object", {})
    order_id = (session.get("metadata") or {}).get("order_id") or session.get("client_reference_id")
    if not order_id or session.get("payment_status") != "paid":
        return
    client = get_supabase_client()
    order_rows = client.table(ORDERS_TABLE).select("order_id,customer_id,order_status,payment_status").eq("order_id", order_id).limit(1).execute().data or []
    if not order_rows:
        return
    order = order_rows[0]
    if order.get("payment_status") == "PAID":
        return
    payment_intent = session.get("payment_intent")
    client.rpc("finalize_stripe_payment", {
        "p_event_id": str(event.get("id", "")),
        "p_event_type": "checkout.session.completed",
        "p_order_id": order_id,
        "p_session_id": str(session.get("id", "")),
        "p_payment_intent": str(payment_intent or ""),
    }).execute()


def _mark_failed_or_expired(event: dict, status: str) -> None:
    session = event.get("data", {}).get("object", {})
    order_id = (session.get("metadata") or {}).get("order_id") or session.get("client_reference_id")
    if not order_id:
        return
    client = get_supabase_client()
    client.table(ORDERS_TABLE).update({"order_status": "CANCELLED", "payment_status": "FAILED"}).eq("order_id", order_id).eq("payment_status", "PENDING").execute()
    client.table(PAYMENTS_TABLE).update({"payment_status": "FAILED"}).eq("payment_reference", session.get("id")).eq("payment_status", "PENDING").execute()


def handle_webhook(payload: bytes, signature: str) -> None:
    """Verify and idempotently process a Stripe webhook event."""
    secret = str(getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        raise CheckoutError("Stripe webhook verification is not configured.")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise CheckoutError("Invalid Stripe webhook signature.") from exc
    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))
    if not event_id:
        raise CheckoutError("Stripe webhook event is missing an ID.")
    client = get_supabase_client()
    if not _claim_event(client, event_id, event_type):
        return
    try:
        if event_type == "checkout.session.completed":
            _mark_paid(event)
        elif event_type in {"checkout.session.expired", "payment_intent.payment_failed"}:
            _mark_failed_or_expired(event, event_type)
        client.table(WEBHOOK_EVENTS_TABLE).update({"processing_status": "PROCESSED"}).eq("event_id", event_id).execute()
    except Exception:
        logger.exception("Stripe webhook event %s failed during processing", event_id)
        raise
