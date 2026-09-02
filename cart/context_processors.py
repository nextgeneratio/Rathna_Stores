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
    try:
        count = get_cart_unit_count(request.session)
    except Exception as exc:
        logger.debug("cart_count context processor failed: %s", exc)
        count = 0
    return {"cart_unit_count": count}
