"""
Cart context processor.

Injects `cart_unit_count` into every template context so the nav badge
always reflects the current cart quantity.

Failure-tolerant: if Supabase is unavailable, the count is 0 and the page
continues to render normally.
"""
import logging

from .services import get_cart_unit_count

logger = logging.getLogger(__name__)


def cart_count(request):
    """Return the total number of units in the current session cart."""
    # Staff templates never render the customer cart control. Avoid a remote
    # cart lookup for every admin page (including the analytics dashboard).
    match = getattr(request, "__dict__", {}).get("resolver_match")
    if getattr(match, "namespace", "") in {"store_admin", "admin"}:
        return {"cart_unit_count": 0}

    # cart_detail has already loaded the complete cart. The view places this
    # value on the request so rendering its base template does not fetch the
    # same cart a second time just for the navigation badge.
    known_count = getattr(request, "__dict__", {}).get("_rathna_cart_unit_count")
    if known_count is not None:
        return {"cart_unit_count": known_count}

    try:
        count = get_cart_unit_count(request.session)
    except Exception as exc:
        logger.debug("cart_count context processor failed: %s", exc)
        count = 0
    return {"cart_unit_count": count}
