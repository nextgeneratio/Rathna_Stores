"""Offline payment workflow used when Stripe is unavailable.

Customers submit a payment reference for a configured offline method. Staff
must verify that payment before the order is marked paid and finalized.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from Rathna_Stores.supabase_client import get_supabase_client
from .stripe_services import (
    CheckoutError,
    ORDERS_TABLE,
    PAYMENTS_TABLE,
    _create_pending_order,
    _finalize_paid_order,
)


MANUAL_PAYMENT_METHODS = {"BANK_TRANSFER", "CASH_ON_DELIVERY"}


def create_offline_payment_order(
    session,
    delivery_type: str,
    address: dict | None = None,
    payment_method: str = "BANK_TRANSFER",
    payment_reference: str = "",
) -> dict:
    """Create a pending order and payment record for staff verification."""
    payment_method = payment_method.strip().upper()
    payment_reference = payment_reference.strip()
    if payment_method not in MANUAL_PAYMENT_METHODS:
        raise CheckoutError("Choose a supported payment method.")
    if len(payment_reference) < 4 or len(payment_reference) > 120:
        raise CheckoutError("Enter the payment or transfer reference shown by your bank.")

    order, checkout, _cart = _create_pending_order(session, delivery_type, address, require_distance=True)
    order_id = str(order["order_id"])
    client = get_supabase_client()
    try:
        client.table(PAYMENTS_TABLE).insert({
            "order_id": order_id,
            "payment_reference": payment_reference,
            "amount": str(checkout["total"]),
            "payment_method": payment_method,
            "payment_status": "PENDING",
        }).execute()
        client.table(ORDERS_TABLE).update({
            "payment_method": payment_method,
            "payment_status": "PENDING",
        }).eq("order_id", order_id).execute()
    except Exception as exc:
        client.table(ORDERS_TABLE).update({
            "order_status": "CANCELLED",
            "payment_status": "FAILED",
        }).eq("order_id", order_id).execute()
        raise CheckoutError("The payment request could not be recorded. Please try again.") from exc

    return {
        "order_id": order_id,
        "order_number": order.get("order_number", order_id),
        "total": checkout["total"],
        "payment_reference": payment_reference,
    }


def confirm_offline_payment(order_id: str) -> dict:
    """Verify and finalize one pending offline payment; safe to repeat."""
    client = get_supabase_client()
    orders = client.table(ORDERS_TABLE).select(
        "order_id,customer_id,order_status,payment_status,delivery_type"
    ).eq("order_id", order_id).limit(1).execute().data or []
    if not orders:
        raise CheckoutError("Order not found.")
    order = orders[0]
    if order.get("payment_status") == "PAID":
        return order
    if order.get("payment_status") != "PENDING":
        raise CheckoutError("Only pending payments can be confirmed.")

    payment_rows = client.table(PAYMENTS_TABLE).select(
        "payment_id,payment_status"
    ).eq("order_id", order_id).eq("payment_status", "PENDING").limit(1).execute().data or []
    if not payment_rows:
        raise CheckoutError("No pending payment was found for this order.")

    # The existing Stripe finalizer is reused for the same stock/cart effects.
    _finalize_paid_order(client, order_id, str(order["customer_id"]))
    now = timezone.now().isoformat()
    client.table(PAYMENTS_TABLE).update({
        "payment_status": "SUCCEEDED",
        "paid_at": now,
        "updated_at": now,
    }).eq("payment_id", payment_rows[0]["payment_id"]).eq("payment_status", "PENDING").execute()
    order_update = {
        "payment_status": "PAID",
        "order_status": "CONFIRMED",
        "updated_at": now,
    }
    if order.get("delivery_type") == "DELIVERY":
        order_update["estimated_delivery_at"] = (timezone.now() + timedelta(hours=24)).isoformat()
    client.table(ORDERS_TABLE).update(order_update).eq("order_id", order_id).eq("payment_status", "PENDING").execute()
    client.table("order_status_history").insert({
        "order_id": order_id,
        "status": "CONFIRMED",
        "note": "Offline payment verified by staff",
    }).execute()
    return {**order, "payment_status": "PAID", "order_status": "CONFIRMED"}


def get_pending_offline_payments() -> list[dict[str, Any]]:
    """Return pending manual payments with their related customer/order data."""
    client = get_supabase_client()
    payments = client.table(PAYMENTS_TABLE).select(
        "payment_id,order_id,payment_reference,amount,payment_method,created_at"
    ).eq("payment_status", "PENDING").order("created_at", desc=True).execute().data or []
    for payment in payments:
        orders = client.table(ORDERS_TABLE).select(
            "order_number,customer_id,total_amount,delivery_type,delivery_city,delivery_district"
        ).eq("order_id", payment["order_id"]).limit(1).execute().data or []
        payment["order"] = orders[0] if orders else {}
    return payments
