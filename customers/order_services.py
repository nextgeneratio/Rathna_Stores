"""Customer-owned order tracking queries."""
from __future__ import annotations

from Rathna_Stores.supabase_client import get_supabase_client


def get_customer_orders(customer_id: str) -> list[dict]:
    client = get_supabase_client()
    return client.table("orders").select("*").eq("customer_id", customer_id).order("created_at", desc=True).execute().data or []


def get_customer_order(customer_id: str, order_id: str) -> dict | None:
    client = get_supabase_client()
    rows = client.table("orders").select("*").eq("customer_id", customer_id).eq("order_id", order_id).limit(1).execute().data or []
    if not rows:
        return None
    order = rows[0]
    order["status_history"] = client.table("order_status_history").select("status,note,changed_at").eq("order_id", order_id).order("changed_at").execute().data or []
    return order