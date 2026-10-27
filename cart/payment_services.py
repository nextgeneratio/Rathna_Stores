"""Offline payment workflow used when Stripe is unavailable.

Customers submit a payment reference for a configured offline method. Staff
must verify that payment before the order is marked paid and finalized.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

import os
import uuid

from Rathna_Stores.supabase_client import get_supabase_client, get_supabase_service_client
from .stripe_services import (
    CheckoutError,
    ORDERS_TABLE,
    PAYMENTS_TABLE,
    _create_pending_order,
    _finalize_paid_order,
)


MANUAL_PAYMENT_METHODS = {"BANK_TRANSFER", "CASH_ON_DELIVERY"}
PAYMENT_METHODS_REQUIRING_MANUAL_VERIFICATION = {"BANK_TRANSFER"}
BANK_SLIP_BUCKET = "Bank_Slip"
MAX_BANK_SLIP_BYTES = 8 * 1024 * 1024
ALLOWED_BANK_SLIP_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}


def create_bank_transfer_order(session, delivery_type: str, address: dict | None, slip) -> dict:
    """Create a pending bank-transfer order and retain its receipt for staff review."""
    if not slip:
        raise CheckoutError("Upload the bank-transfer slip before submitting your order.")
    content_type = str(getattr(slip, "content_type", "")).lower()
    if content_type not in ALLOWED_BANK_SLIP_TYPES:
        raise CheckoutError("Bank slip must be a JPG, PNG, WebP image, or PDF.")
    if getattr(slip, "size", 0) > MAX_BANK_SLIP_BYTES:
        raise CheckoutError("Bank slip must be 8 MB or smaller.")
    file_bytes = slip.read()
    if not file_bytes:
        raise CheckoutError("The uploaded bank slip is empty.")

    order, checkout, _cart = _create_pending_order(session, delivery_type, address, require_distance=True)
    order_id = str(order["order_id"])
    payment_id = str(uuid.uuid4())
    storage_path = f"orders/{order_id}/{uuid.uuid4().hex}.{ALLOWED_BANK_SLIP_TYPES[content_type]}"
    client = get_supabase_client()
    try:
        # A service-role client is used only on the server; browser clients never
        # receive permission to browse another customer's proof of payment.
        storage = get_supabase_service_client()
        storage.storage.from_(BANK_SLIP_BUCKET).upload(storage_path, file_bytes, {
            "content-type": content_type, "upsert": "false",
        })
        client.table(PAYMENTS_TABLE).insert({
            "payment_id": payment_id, "order_id": order_id,
            "payment_reference": f"bank:{order_id}", "amount": str(checkout["total"]),
            "payment_method": "BANK_TRANSFER", "payment_status": "PENDING",
        }).execute()
        client.table("bank_payment_slips").insert({
            "payment_id": payment_id, "order_id": order_id, "storage_bucket": BANK_SLIP_BUCKET,
            "storage_path": storage_path, "original_filename": os.path.basename(getattr(slip, "name", "bank-slip")),
            "content_type": content_type, "file_size_bytes": len(file_bytes), "review_status": "PENDING",
        }).execute()
        client.table(ORDERS_TABLE).update({"payment_method": "BANK_TRANSFER", "payment_status": "PENDING"}).eq("order_id", order_id).execute()
    except Exception as exc:
        client.table(ORDERS_TABLE).update({"order_status": "CANCELLED", "payment_status": "FAILED"}).eq("order_id", order_id).execute()
        raise CheckoutError("We could not save your bank slip. Please try again.") from exc
    return {"order_id": order_id, "order_number": order.get("order_number", order_id), "total": checkout["total"]}


def _refresh_customer_spend(client, customer_id: str) -> None:
    """Keep the customer aggregate aligned with all paid order snapshots."""
    paid_orders = client.table(ORDERS_TABLE).select("total_amount").eq(
        "customer_id", customer_id
    ).eq("payment_status", "PAID").execute().data or []
    total_spend = sum(
        (Decimal(str(order.get("total_amount") or "0")) for order in paid_orders),
        Decimal("0.00"),
    )
    client.table("customers").update({
        "total_spend": str(total_spend.quantize(Decimal("0.01"))),
    }).eq("customer_id", customer_id).execute()


def create_offline_payment_order(
    session,
    delivery_type: str,
    address: dict | None = None,
    payment_method: str = "BANK_TRANSFER",
    payment_reference: str = "",
) -> dict:
    """Complete the simulated payment and place the order immediately."""
    payment_method = payment_method.strip().upper()
    payment_reference = payment_reference.strip()
    if payment_method not in MANUAL_PAYMENT_METHODS:
        raise CheckoutError("Choose a supported payment method.")

    order, checkout, _cart = _create_pending_order(session, delivery_type, address, require_distance=True)
    order_id = str(order["order_id"])
    payment_reference = payment_reference or f"simulated:{order_id}"
    client = get_supabase_client()
    now = timezone.now().isoformat()
    try:
        client.table(PAYMENTS_TABLE).insert({
            "order_id": order_id,
            "payment_reference": payment_reference,
            "amount": str(checkout["total"]),
            "payment_method": payment_method,
            "payment_status": "SUCCEEDED",
            "paid_at": now,
        }).execute()
        _finalize_paid_order(client, order_id, str(order["customer_id"]))
        client.table(ORDERS_TABLE).update({
            "payment_method": payment_method,
            "payment_status": "PAID",
            "order_status": "CONFIRMED",
            "updated_at": now,
        }).eq("order_id", order_id).execute()
        client.table("order_status_history").insert({
            "order_id": order_id,
            "status": "CONFIRMED",
            "note": "Simulated payment completed",
        }).execute()
        _refresh_customer_spend(client, str(order["customer_id"]))
    except Exception as exc:
        client.table(ORDERS_TABLE).update({
            "order_status": "CANCELLED",
            "payment_status": "FAILED",
        }).eq("order_id", order_id).execute()
        client.table(PAYMENTS_TABLE).update({
            "payment_status": "FAILED",
        }).eq("payment_reference", payment_reference).execute()
        raise CheckoutError("The payment request could not be recorded. Please try again.") from exc

    return {
        "order_id": order_id,
        "order_number": order.get("order_number", order_id),
        "total": checkout["total"],
        "payment_reference": payment_reference,
        "payment_status": "PAID",
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
    ).eq("order_id", order_id).eq("payment_status", "PENDING").in_(
        "payment_method", sorted(PAYMENT_METHODS_REQUIRING_MANUAL_VERIFICATION)
    ).limit(1).execute().data or []
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
        "order_status": "PROCESSING" if order.get("delivery_type") == "PICKUP" else "OUT_FOR_DELIVERY",
        "updated_at": now,
    }
    if order.get("delivery_type") == "DELIVERY":
        order_update["estimated_delivery_at"] = (timezone.now() + timedelta(hours=3)).isoformat()
    else:
        order_update["pickup_ready_at"] = (timezone.now() + timedelta(hours=1)).isoformat()
    client.table(ORDERS_TABLE).update(order_update).eq("order_id", order_id).eq("payment_status", "PENDING").execute()
    client.table("order_status_history").insert({
        "order_id": order_id,
        "status": order_update["order_status"],
        "note": "Offline payment verified by staff",
    }).execute()
    client.table("bank_payment_slips").update({
        "review_status": "APPROVED", "reviewed_at": now,
    }).eq("payment_id", payment_rows[0]["payment_id"]).eq("review_status", "PENDING").execute()
    _refresh_customer_spend(client, str(order["customer_id"]))
    return {**order, "payment_status": "PAID", "order_status": order_update["order_status"]}


def get_pending_offline_payments() -> list[dict[str, Any]]:
    """Return pending bank-transfer payments awaiting staff receipt verification."""
    client = get_supabase_client()
    payments = client.table(PAYMENTS_TABLE).select(
        "payment_id,order_id,payment_reference,amount,payment_method,created_at"
    ).eq("payment_status", "PENDING").in_(
        "payment_method", sorted(PAYMENT_METHODS_REQUIRING_MANUAL_VERIFICATION)
    ).order("created_at", desc=True).execute().data or []
    for payment in payments:
        orders = client.table(ORDERS_TABLE).select(
            "order_number,customer_id,total_amount,delivery_type,delivery_city,delivery_district"
        ).eq("order_id", payment["order_id"]).limit(1).execute().data or []
        payment["order"] = orders[0] if orders else {}
        slips = client.table("bank_payment_slips").select(
            "slip_id,storage_bucket,storage_path,original_filename,review_status"
        ).eq("payment_id", payment["payment_id"]).limit(1).execute().data or []
        payment["slip"] = slips[0] if slips else None
        if payment["slip"]:
            try:
                signed = get_supabase_service_client().storage.from_(payment["slip"]["storage_bucket"]).create_signed_url(
                    payment["slip"]["storage_path"], 600
                )
                payment["slip_url"] = signed.get("signedURL") or signed.get("signedUrl")
            except Exception:
                payment["slip_url"] = None
    return payments
