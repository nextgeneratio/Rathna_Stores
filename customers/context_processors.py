"""
Customer context processor — injects authenticated customer state into every
template context. Failure-tolerant: always returns a dict, never raises.
"""
import logging
from .services import get_customer_nav_context

logger = logging.getLogger(__name__)


def customer_auth(request):
    """
    Inject customer authentication state for the navigation control.
    Returns:
      customer_authenticated: bool
      nav_customer: dict|None  (customer_id, first_name, last_name, profile_image_url)
    """
    try:
        ctx = get_customer_nav_context(request.session)
        return {
            "customer_authenticated": ctx.get("customer_authenticated", False),
            "nav_customer": ctx.get("customer"),
        }
    except Exception as exc:
        logger.debug("customer_auth context processor failed: %s", exc)
        return {"customer_authenticated": False, "nav_customer": None}
