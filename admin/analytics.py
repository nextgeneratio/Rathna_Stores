"""Staff-only analytics and product-intelligence service functions."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from Rathna_Stores.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

PURCHASE_WINDOW_DAYS = 30
MAX_REPORT_DAYS = 90
QUALIFYING_ORDER_STATUSES = ("DELIVERED", "COMPLETED")
QUALIFYING_PAYMENT_STATUS = "PAID"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class AnalyticsError(Exception):
    """Raised when staff analytics cannot be loaded safely."""


def _rows(table: str, columns: str, *filters: tuple[str, str, Any]) -> list[dict]:
    client = get_supabase_client()
    query = client.table(table).select(columns)
    for method, field, value in filters:
        query = getattr(query, method)(field, value)
    response = query.execute()
    return list(getattr(response, "data", None) or [])


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timezone.is_naive(result):
        result = timezone.make_aware(result, timezone.get_current_timezone())
    return result


def parse_report_range(start_text: str = "", end_text: str = "", days: int = PURCHASE_WINDOW_DAYS) -> tuple[date, date]:
    """Parse an inclusive local-date range, bounded to the report maximum."""
    today = timezone.localdate()
    try:
        end = date.fromisoformat(end_text) if end_text else today
        start = date.fromisoformat(start_text) if start_text else end - timedelta(days=days - 1)
    except ValueError as exc:
        raise AnalyticsError("Use valid ISO dates for the report range.") from exc
    if end < start:
        raise AnalyticsError("The report end date must be on or after its start date.")
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        raise AnalyticsError(f"Report ranges cannot exceed {MAX_REPORT_DAYS} days.")
    return start, end


def _qualifying_orders(start: date, end: date) -> list[dict]:
    rows = _rows(
        "orders",
        "order_id,customer_id,order_status,payment_status,created_at,order_date,total_amount",
        ("gte", "created_at", start.isoformat()),
        ("lt", "created_at", (end + timedelta(days=1)).isoformat()),
    )
    return [
        row for row in rows
        if row.get("order_status") in QUALIFYING_ORDER_STATUSES
        and row.get("payment_status") == QUALIFYING_PAYMENT_STATUS
    ]


def _empty_metrics() -> dict[str, Any]:
    return {
        "order_count": 0, "unique_purchasers": 0, "gross_sales": Decimal("0.00"),
        "average_order_value": Decimal("0.00"), "trend": [], "best_sellers": [],
        "review_metrics": {"total": 0, "approved": 0, "pending": 0, "average": None, "products": []},
        "customer_activity": {"registered": 0, "active_purchasers": 0, "repeat_purchasers": 0, "new_purchasers": 0},
        "observations": [],
    }


def _period_metrics(start: date, end: date, orders: list[dict], items: list[dict], reviews: list[dict], customers: list[dict], products: list[dict]) -> dict[str, Any]:
    metrics = _empty_metrics()
    customer_orders: defaultdict[str, int] = defaultdict(int)
    for order in orders:
        customer_orders[str(order.get("customer_id"))] += 1
    metrics["order_count"] = len(orders)
    metrics["unique_purchasers"] = len(customer_orders)
    metrics["gross_sales"] = sum((Decimal(str(order.get("total_amount") or "0")) for order in orders), Decimal("0.00"))
    if orders:
        metrics["average_order_value"] = metrics["gross_sales"] / len(orders)

    order_ids = {str(order.get("order_id")) for order in orders}
    period_items = [item for item in items if str(item.get("order_id")) in order_ids]
    product_map = {str(product.get("product_id")): product for product in products}
    grouped: dict[str, dict[str, Any]] = {}
    for item in period_items:
        product_id = str(item.get("product_id"))
        entry = grouped.setdefault(product_id, {"product_id": product_id, "product_name": item.get("product_name_snapshot", "Unknown product"), "units_sold": 0, "purchase_count": 0, "sales_value": Decimal("0.00"), "current_stock": product_map.get(product_id, {}).get("stock_quantity")})
        entry["units_sold"] += int(item.get("quantity") or 0)
        entry["sales_value"] += Decimal(str(item.get("item_total") or "0"))
        entry["purchase_count"] += 1
    metrics["best_sellers"] = sorted(grouped.values(), key=lambda item: (-item["units_sold"], -item["sales_value"], str(item["product_name"]).lower()))

    trend: defaultdict[str, dict[str, Decimal | int]] = defaultdict(lambda: {"orders": 0, "sales": Decimal("0.00")})
    for order in orders:
        stamp = _as_datetime(order.get("order_date") or order.get("created_at"))
        if stamp:
            key = timezone.localtime(stamp).date().isoformat()
            trend[key]["orders"] += 1
            trend[key]["sales"] += Decimal(str(order.get("total_amount") or "0"))
    cursor = start
    while cursor <= end:
        value = trend[cursor.isoformat()]
        metrics["trend"].append({"date": cursor.isoformat(), "orders": value["orders"], "sales": value["sales"]})
        cursor += timedelta(days=1)

    approved = [review for review in reviews if review.get("is_approved")]
    product_reviews: defaultdict[str, list[int]] = defaultdict(list)
    for review in approved:
        product_reviews[str(review.get("product_id"))].append(int(review.get("rating") or 0))
    metrics["review_metrics"] = {
        "total": len(reviews), "approved": len(approved), "pending": len(reviews) - len(approved),
        "average": (sum(int(review.get("rating") or 0) for review in approved) / len(approved)) if approved else None,
        "products": [
            {"product_id": product_id, "product_name": product_map.get(product_id, {}).get("name", "Unknown product"), "count": len(ratings), "average": sum(ratings) / len(ratings)}
            for product_id, ratings in sorted(product_reviews.items(), key=lambda pair: str(product_map.get(pair[0], {}).get("name", "")).lower())
        ],
    }
    active_customers = [customer for customer in customers if customer.get("is_active")]
    metrics["customer_activity"] = {
        "registered": len(active_customers),
        "active_purchasers": len(customer_orders),
        "repeat_purchasers": sum(1 for count in customer_orders.values() if count >= 2),
        "new_purchasers": sum(1 for customer_id in customer_orders if sum(1 for order in orders if str(order.get("customer_id")) == customer_id) == 1),
    }
    if not orders:
        metrics["observations"].append("No qualifying paid and completed purchases exist for this period.")
    return metrics


def get_dashboard_intelligence() -> dict[str, Any]:
    """Return dashboard counts; failures are surfaced so the view can warn staff."""
    cache_key = "rathna_staff_dashboard_intelligence"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    today = timezone.localdate()
    start = today - timedelta(days=PURCHASE_WINDOW_DAYS - 1)
    customers = _rows("customers", "customer_id,is_active")
    orders = _qualifying_orders(start, today)
    result = {
        "registered_customer_count": sum(1 for customer in customers if customer.get("is_active")),
        "active_purchaser_count": len({str(order.get("customer_id")) for order in orders}),
        "purchase_window_days": PURCHASE_WINDOW_DAYS,
        "has_qualifying_purchases": bool(orders),
    }
    cache.set(
        cache_key,
        result,
        timeout=getattr(settings, "STAFF_DASHBOARD_CACHE_SECONDS", 20),
    )
    return result


def clear_dashboard_intelligence_cache() -> None:
    """Invalidate staff aggregate counts after a known relevant write."""
    cache.delete("rathna_staff_dashboard_intelligence")


def get_analytics_report(start: date, end: date) -> dict[str, Any]:
    """Load all report inputs in batches and aggregate them in memory."""
    orders = _qualifying_orders(start, end)
    items = _rows("order_items", "order_id,product_id,product_name_snapshot,quantity,item_total") if orders else []
    products = _rows("products", "product_id,name,stock_quantity,is_active")
    reviews = _rows("reviews", "product_id,rating,is_approved")
    customers = _rows("customers", "customer_id,is_active")
    current = _period_metrics(start, end, orders, items, reviews, customers, products)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - (end - start)
    previous_orders = _qualifying_orders(previous_start, previous_end)
    previous = _period_metrics(previous_start, previous_end, previous_orders, [], [], customers, products)
    current["comparison"] = {"previous_start": previous_start, "previous_end": previous_end, "order_count": previous["order_count"], "gross_sales": previous["gross_sales"]}
    current["range"] = {"start": start, "end": end}
    return current


def build_opportunity_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    """Create an aggregate-only AI input snapshot."""
    return {
        "report_range": {key: value.isoformat() for key, value in report["range"].items()},
        "top_products": [{key: item.get(key) for key in ("product_name", "units_sold", "purchase_count", "sales_value", "current_stock")} for item in report["best_sellers"][:5]],
        "review_summary": report["review_metrics"],
        "customer_activity": report["customer_activity"],
        "observations": report["observations"],
    }


def request_product_opportunities(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask OpenRouter for strictly structured, non-mutating staff suggestions."""
    api_key = str(getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()
    if not api_key:
        raise AnalyticsError("AI opportunities are not configured.")
    prompt = json.dumps(snapshot, default=str, separators=(",", ":"))
    system = '{"suggestions":[{"product_or_category":"string","rationale":"string","supporting_signals":["string"],"target_segment":"aggregate segment","confidence":"low|medium|high","cautions":["string"]}]}'
    try:
        response = httpx.post(OPENROUTER_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": getattr(settings, "OPENROUTER_MODEL", ""), "temperature": 0.2, "max_tokens": 900, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": "Return only JSON matching this schema: " + system}, {"role": "user", "content": prompt}]}, timeout=15)
        response.raise_for_status()
        data = response.json()
        result = json.loads(data["choices"][0]["message"]["content"])
        suggestions = result.get("suggestions")
        if not isinstance(suggestions, list) or any(not isinstance(item, dict) or not all(key in item for key in ("product_or_category", "rationale", "supporting_signals", "target_segment", "confidence", "cautions")) for item in suggestions):
            raise ValueError("invalid suggestion schema")
        return suggestions[:8]
    except Exception as exc:
        logger.error("Staff product-opportunity request failed: %s", exc)
        raise AnalyticsError("The opportunity service is temporarily unavailable. Please retry.") from exc
