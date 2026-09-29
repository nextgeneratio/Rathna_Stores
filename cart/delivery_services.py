"""Simulated distance-based delivery quotes and customer ETA helpers."""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class DeliveryError(Exception):
    """A delivery quote or destination validation failure."""


def _decimal_setting(name: str, default: str) -> Decimal:
    return Decimal(str(getattr(settings, name, default)))


def _coordinate(address: dict[str, Any], name: str) -> float:
    try:
        value = float(address.get(name))
    except (TypeError, ValueError):
        raise DeliveryError("Use the location button to share coordinates for delivery.")
    valid = -90 <= value <= 90 if name == "latitude" else -180 <= value <= 180
    if not valid:
        raise DeliveryError("The delivery location coordinates are invalid.")
    return value


def distance_km(address: dict[str, Any]) -> Decimal:
    """Return the great-circle distance from the store to a saved address."""
    latitude = math.radians(_coordinate(address, "latitude"))
    longitude = math.radians(_coordinate(address, "longitude"))
    store_latitude = math.radians(float(getattr(settings, "STORE_LATITUDE", "6.9271")))
    store_longitude = math.radians(float(getattr(settings, "STORE_LONGITUDE", "79.8612")))
    delta_latitude = latitude - store_latitude
    delta_longitude = longitude - store_longitude
    haversine = math.sin(delta_latitude / 2) ** 2 + math.cos(store_latitude) * math.cos(latitude) * math.sin(delta_longitude / 2) ** 2
    return Decimal(str(6371 * 2 * math.asin(math.sqrt(haversine)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def delivery_quote(address: dict[str, Any]) -> dict[str, Any]:
    """Calculate a simulated base-plus-per-kilometre delivery quote."""
    if str(address.get("district", "")).strip().lower() != "colombo":
        raise DeliveryError("Delivery is available only in Colombo district.")
    kilometres = distance_km(address)
    base_fee = _decimal_setting("DELIVERY_BASE_FEE_LKR", "150.00")
    rate = _decimal_setting("DELIVERY_RATE_PER_KM_LKR", "80.00")
    fee = (base_fee + (kilometres * rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    estimated_at = timezone.now() + timedelta(hours=int(getattr(settings, "DELIVERY_ESTIMATE_HOURS", 24)))
    return {"distance_km": kilometres, "fee": fee, "estimated_at": estimated_at}
