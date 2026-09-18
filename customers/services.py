"""
Customer service layer — Supabase Auth integration, customer profile/address
CRUD, profile image upload, and guest-to-customer cart merge.

Security rules:
- Passwords are handled by Supabase Auth only; never stored or logged here.
- The authenticated customer_id (customers.customer_id) is the sole authority
  for profile, address, cart, and future checkout access.
- Never accept a customer_id, auth_user_id, or storage path from the browser
  as proof of ownership. All ownership is resolved server-side from the
  authenticated session state.
- Service-role or private keys must not appear here. The anon client key is
  used for Auth flows, which is correct per Supabase documentation.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from datetime import date
from typing import Optional

from postgrest.exceptions import APIError

from Rathna_Stores.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# ── Table / bucket constants ──────────────────────────────────────────────────

CUSTOMERS_TABLE = "customers"
ADDRESSES_TABLE = "customer_addresses"
PROFILE_BUCKET = "User_Profile"

# ── Session keys ──────────────────────────────────────────────────────────────

# The authenticated customer's customers.customer_id UUID (string)
CUSTOMER_ID_SESSION_KEY = "rathna_customer_id"
# The Supabase Auth access token
ACCESS_TOKEN_SESSION_KEY = "rathna_auth_token"
# The Supabase Auth refresh token
REFRESH_TOKEN_SESSION_KEY = "rathna_refresh_token"

# Re-export the guest cart session key so tests can import it from one place
from cart.services import SESSION_KEY  # noqa: E402 (placed after constants)

# ── Allowed MIME types for profile images ────────────────────────────────────

ALLOWED_PROFILE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PROFILE_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB

# ── Domain exceptions ─────────────────────────────────────────────────────────


class AuthError(Exception):
    """User-facing authentication/registration failure."""


class CustomerError(Exception):
    """User-facing customer profile/address operation failure."""


class MergeError(Exception):
    """Guest-to-customer cart merge failure (retryable)."""


# ── Session helpers ───────────────────────────────────────────────────────────

def get_authenticated_customer_id(session) -> Optional[str]:
    """Return the authenticated customer_id from session, or None."""
    return session.get(CUSTOMER_ID_SESSION_KEY)


def is_customer_authenticated(session) -> bool:
    return bool(session.get(CUSTOMER_ID_SESSION_KEY))


def _set_customer_session(session, customer_id: str, access_token: str, refresh_token: str) -> None:
    """Store minimum authentication state in the Django session."""
    session[CUSTOMER_ID_SESSION_KEY] = customer_id
    session[ACCESS_TOKEN_SESSION_KEY] = access_token
    session[REFRESH_TOKEN_SESSION_KEY] = refresh_token
    if hasattr(session, "modified"):
        session.modified = True


def clear_customer_session(session) -> None:
    """Remove all customer authentication state from the session."""
    for key in (CUSTOMER_ID_SESSION_KEY, ACCESS_TOKEN_SESSION_KEY, REFRESH_TOKEN_SESSION_KEY):
        session.pop(key, None)
    if hasattr(session, "modified"):
        session.modified = True


# ── Input validation helpers ──────────────────────────────────────────────────

def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise AuthError("Please enter a valid email address.")
    return email


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")


def _validate_name(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise CustomerError(f"{label} is required.")
    if len(value) > 100:
        raise CustomerError(f"{label} must be 100 characters or fewer.")
    return value


def _validate_date_of_birth(raw: str) -> Optional[str]:
    """Validate and return an ISO date string, or None if empty."""
    raw = raw.strip() if raw else ""
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise CustomerError("Date of birth must be a valid date (YYYY-MM-DD).")
    if parsed >= date.today():
        raise CustomerError("Date of birth must be in the past.")
    return parsed.isoformat()


def _safe_next(next_url: str) -> str:
    """Return next_url if it is a safe same-site relative path, else '/'."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


# ── Supabase Auth helpers ─────────────────────────────────────────────────────

def _resolve_customer_from_auth(auth_user_id: str) -> Optional[dict]:
    """
    Look up the customers row linked to the given Supabase Auth user ID.
    Returns the row dict or None.
    """
    try:
        client = get_supabase_client()
        resp = (
            client.table(CUSTOMERS_TABLE)
            .select("customer_id, first_name, last_name, email, is_active, profile_image_url")
            .eq("auth_user_id", auth_user_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except APIError as exc:
        logger.error("Customer lookup by auth_user_id %s failed: %s", auth_user_id, exc)
        return None


# ── Registration ──────────────────────────────────────────────────────────────

def register_customer(
    session,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    password_confirm: str,
    phone_number: str = "",
    is_email_notifications_enabled: bool = True,
    is_sms_notifications_enabled: bool = False,
) -> dict:
    """
    Register a new customer via Supabase Auth, create the customers row,
    and establish the session.

    Returns a dict with keys:
      - "customer": the created customers row
      - "email_confirmation_required": bool — True when Supabase requires
        email verification before the account is usable.
      - "session_established": bool — True when the customer is immediately
        signed in (no email confirmation needed).

    Raises AuthError on any failure.
    """
    # Validate inputs before touching Supabase
    email = _validate_email(email)
    first_name = _validate_name(first_name, "First name")
    last_name = _validate_name(last_name, "Last name")
    _validate_password(password)
    if password != password_confirm:
        raise AuthError("Passwords do not match.")

    client = get_supabase_client()

    # Step 1: Create Supabase Auth identity
    try:
        auth_resp = client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {
                    "first_name": first_name,
                    "last_name": last_name,
                }
            },
        })
    except Exception as exc:
        err_msg = str(exc).lower()
        if "already registered" in err_msg or "already exists" in err_msg or "unique" in err_msg:
            # Generic message — do not reveal whether the email is registered
            raise AuthError("An account with this email already exists, or the email is invalid.")
        logger.error("Supabase Auth sign_up failed for %s: %s", email, exc)
        raise AuthError("Registration is temporarily unavailable. Please try again.") from exc

    if not auth_resp or not auth_resp.user:
        raise AuthError("Registration failed. Please try again.")

    auth_user = auth_resp.user
    auth_user_id = str(auth_user.id)

    # Detect email confirmation requirement:
    # When email confirmation is enabled, auth_resp.session is None
    session_data = getattr(auth_resp, "session", None)
    email_confirmation_required = session_data is None

    # Step 2: Create the customers row
    customer_data = {
        "auth_user_id": auth_user_id,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "is_email_notifications_enabled": is_email_notifications_enabled,
        "is_sms_notifications_enabled": is_sms_notifications_enabled,
    }
    if phone_number:
        customer_data["phone_number"] = phone_number.strip()

    try:
        insert_resp = client.table(CUSTOMERS_TABLE).insert(customer_data).execute()
        customer_rows = insert_resp.data or []
        if not customer_rows:
            raise CustomerError("Profile creation returned no data.")
        customer = customer_rows[0]
    except (APIError, CustomerError) as exc:
        logger.error("customers row creation failed for auth_user %s: %s", auth_user_id, exc)
        # Auth identity was created but profile creation failed.
        # Document as a known recovery path — the admin can link the orphaned
        # auth identity to a customers row using auth_user_id.
        raise AuthError(
            "Your account was created but your profile could not be saved. "
            "Please contact support with your email address."
        ) from exc

    # Step 3: Establish session if Supabase returned a session
    session_established = False
    if not email_confirmation_required and session_data:
        try:
            _set_customer_session(
                session,
                customer["customer_id"],
                session_data.access_token,
                session_data.refresh_token,
            )
            session_established = True
        except Exception as exc:
            logger.error("Session establishment after registration failed: %s", exc)
            # Not fatal — customer can log in manually

    return {
        "customer": customer,
        "email_confirmation_required": email_confirmation_required,
        "session_established": session_established,
    }


# ── Login ─────────────────────────────────────────────────────────────────────

def login_customer(session, email: str, password: str) -> dict:
    """
    Authenticate a customer via Supabase Auth email/password.
    Establishes the Django session with customer_id and tokens.

    Returns the customers row dict on success.
    Raises AuthError on any failure (generic message for invalid credentials).
    """
    email = _validate_email(email)
    if not password:
        raise AuthError("Password is required.")

    client = get_supabase_client()

    try:
        auth_resp = client.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
    except Exception as exc:
        err_msg = str(exc).lower()
        if any(k in err_msg for k in ("invalid", "email not confirmed", "wrong", "credentials", "not found")):
            raise AuthError("Invalid email or password.")
        logger.error("Supabase Auth sign_in failed: %s", exc)
        raise AuthError("Login is temporarily unavailable. Please try again.") from exc

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise AuthError("Invalid email or password.")

    auth_user_id = str(auth_resp.user.id)
    session_data = auth_resp.session

    # Resolve the customers row
    customer = _resolve_customer_from_auth(auth_user_id)
    if not customer:
        logger.error("No customers row found for auth_user_id %s after successful login", auth_user_id)
        raise AuthError("Your account profile could not be found. Please contact support.")

    if not customer.get("is_active", True):
        raise AuthError("This account has been deactivated. Please contact support.")

    _set_customer_session(
        session,
        customer["customer_id"],
        session_data.access_token,
        session_data.refresh_token,
    )

    return customer


def login_customer_with_tokens(
    session,
    access_token: str,
    refresh_token: str = "",
) -> dict:
    """Validate Supabase callback tokens and establish the Django session."""
    if not access_token:
        raise AuthError("The verification link is invalid or has expired.")

    client = get_supabase_client()
    try:
        auth_resp = client.auth.get_user(access_token)
    except Exception as exc:
        logger.info("Supabase callback token validation failed: %s", exc)
        raise AuthError("The verification link is invalid or has expired.") from exc

    auth_user = getattr(auth_resp, "user", None)
    if not auth_user or not getattr(auth_user, "id", None):
        raise AuthError("The verification link is invalid or has expired.")

    customer = _resolve_customer_from_auth(str(auth_user.id))
    if not customer:
        logger.error("No customers row found for verified auth_user %s", auth_user.id)
        raise AuthError("Your account profile could not be found. Please contact support.")
    if not customer.get("is_active", True):
        raise AuthError("This account has been deactivated. Please contact support.")

    _set_customer_session(session, customer["customer_id"], access_token, refresh_token)
    return customer


# ── Logout ────────────────────────────────────────────────────────────────────

def logout_customer(session) -> None:
    """
    Sign out from Supabase Auth (best-effort) and clear all customer session state.
    Must not raise — logout must always succeed locally even if Supabase call fails.
    """
    access_token = session.get(ACCESS_TOKEN_SESSION_KEY)
    if access_token:
        try:
            client = get_supabase_client()
            client.auth.sign_out()
        except Exception as exc:
            logger.debug("Supabase sign_out call failed (non-fatal): %s", exc)

    clear_customer_session(session)


# ── Customer profile reads ────────────────────────────────────────────────────

def get_customer_profile(customer_id: str) -> Optional[dict]:
    """Return the full customers row for the given customer_id, or None."""
    try:
        uuid.UUID(customer_id)
    except (ValueError, AttributeError):
        return None
    try:
        client = get_supabase_client()
        resp = (
            client.table(CUSTOMERS_TABLE)
            .select("*")
            .eq("customer_id", customer_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except APIError as exc:
        logger.error("get_customer_profile failed for %s: %s", customer_id, exc)
        return None


# ── Customer profile writes ───────────────────────────────────────────────────

def update_customer_profile(customer_id: str, data: dict) -> dict:
    """
    Update allowed editable fields on a customers row.
    Protected fields (customer_id, auth_user_id, loyalty_points, etc.) are
    stripped before the update — they cannot be changed through this function.
    Returns the updated row.
    Raises CustomerError on failure.
    """
    _PROTECTED = {
        "customer_id", "auth_user_id", "loyalty_points", "total_spend",
        "is_member", "member_since", "is_active", "created_at", "updated_at",
        "profile_image_url",  # managed only through upload workflow
    }
    safe_data = {k: v for k, v in data.items() if k not in _PROTECTED}
    if not safe_data:
        raise CustomerError("No updatable fields provided.")

    try:
        client = get_supabase_client()
        resp = (
            client.table(CUSTOMERS_TABLE)
            .update(safe_data)
            .eq("customer_id", customer_id)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise CustomerError("Profile update returned no data.")
        return rows[0]
    except CustomerError:
        raise
    except APIError as exc:
        logger.error("update_customer_profile failed for %s: %s", customer_id, exc)
        raise CustomerError("Could not update your profile. Please try again.") from exc


# ── Profile image ─────────────────────────────────────────────────────────────

def _safe_mime_from_bytes(data: bytes) -> Optional[str]:
    """Sniff MIME type from file magic bytes (first 12 bytes)."""
    if data[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "image/webp"
    return None


def upload_profile_image(
    customer_id: str,
    file_bytes: bytes,
    declared_content_type: str,
    access_token: str = "",
    refresh_token: str = "",
) -> str:
    """
    Upload a profile image to the User_Profile bucket, update customers row.
    Returns the new profile_image_url.
    Raises CustomerError on validation or upload failure.
    """
    # Server-side MIME validation (magic bytes first, then declared type as fallback)
    sniffed = _safe_mime_from_bytes(file_bytes)
    content_type = sniffed or declared_content_type
    if content_type not in ALLOWED_PROFILE_MIME_TYPES:
        raise CustomerError("Profile image must be a JPEG, PNG, or WebP file.")

    if len(file_bytes) > MAX_PROFILE_IMAGE_BYTES:
        raise CustomerError("Profile image must be smaller than 4 MB.")

    ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    ext = ext_map[content_type]
    storage_path = f"customers/{customer_id}/profile.{ext}"

    client = get_supabase_client(access_token, refresh_token)

    # Attempt upload (upsert=True to replace any existing file)
    try:
        client.storage.from_(PROFILE_BUCKET).upload(
            storage_path,
            file_bytes,
            {"content-type": content_type, "upsert": "true"},
        )
    except Exception as exc:
        logger.error("Profile image upload failed for %s: %s", customer_id, exc)
        raise CustomerError("Image upload failed. Please try again.") from exc

    # Get public/accessible URL
    try:
        image_url = client.storage.from_(PROFILE_BUCKET).get_public_url(storage_path)
    except Exception as exc:
        logger.warning("Could not get public URL for profile image %s: %s", storage_path, exc)
        image_url = storage_path  # fallback — display may fail but data is not lost

    # Update customers row
    try:
        resp = (
            client.table(CUSTOMERS_TABLE)
            .update({"profile_image_url": image_url})
            .eq("customer_id", customer_id)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise CustomerError("Profile image URL could not be saved.")
    except CustomerError:
        raise
    except APIError as exc:
        # Upload succeeded but DB write failed — attempt to clean up storage
        logger.error("Profile image DB write failed for %s: %s", customer_id, exc)
        _delete_profile_storage_object(storage_path)
        raise CustomerError("Image was uploaded but could not be saved. Please try again.") from exc

    return image_url


def delete_profile_image(
    customer_id: str,
    access_token: str = "",
    refresh_token: str = "",
) -> None:
    """
    Remove the profile image from storage and clear profile_image_url.
    Best-effort storage cleanup — always clears the DB field.
    Raises CustomerError if DB update fails.
    """
    # Fetch the existing profile to know the stored path
    profile = get_customer_profile(customer_id)
    if not profile:
        raise CustomerError("Customer not found.")

    image_url = profile.get("profile_image_url") or ""

    # Clear the DB field first
    try:
        client = get_supabase_client(access_token, refresh_token)
        client.table(CUSTOMERS_TABLE).update(
            {"profile_image_url": None}
        ).eq("customer_id", customer_id).execute()
    except APIError as exc:
        logger.error("Profile image URL clear failed for %s: %s", customer_id, exc)
        raise CustomerError("Could not remove profile image. Please try again.") from exc

    # Best-effort storage cleanup
    if image_url:
        marker = f"/{PROFILE_BUCKET}/"
        if marker in image_url:
            storage_path = image_url.split(marker, 1)[-1].split("?")[0]
            _delete_profile_storage_object(storage_path, access_token, refresh_token)


def _delete_profile_storage_object(
    storage_path: str,
    access_token: str = "",
    refresh_token: str = "",
) -> None:
    """Delete a User_Profile bucket object. Logs a warning on failure."""
    try:
        client = get_supabase_client(access_token, refresh_token)
        client.storage.from_(PROFILE_BUCKET).remove([storage_path])
    except Exception as exc:
        logger.warning("Profile storage cleanup failed for %s: %s", storage_path, exc)


# ── Customer addresses ────────────────────────────────────────────────────────

def get_customer_addresses(customer_id: str) -> list[dict]:
    """Return all addresses for the authenticated customer, ordered default-first."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(ADDRESSES_TABLE)
            .select("*")
            .eq("customer_id", customer_id)
            .order("is_default", desc=True)
            .execute()
        )
        return resp.data or []
    except APIError as exc:
        logger.error("get_customer_addresses failed for %s: %s", customer_id, exc)
        return []


def get_address_by_id(customer_id: str, address_id: str) -> Optional[dict]:
    """
    Return a single address row, verifying it belongs to customer_id.
    Returns None if not found or not owned by this customer.
    """
    try:
        uuid.UUID(address_id)
    except (ValueError, AttributeError):
        return None
    try:
        client = get_supabase_client()
        resp = (
            client.table(ADDRESSES_TABLE)
            .select("*")
            .eq("address_id", address_id)
            .eq("customer_id", customer_id)  # ownership filter
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except APIError as exc:
        logger.error("get_address_by_id failed for %s/%s: %s", customer_id, address_id, exc)
        return None


def _EDITABLE_ADDRESS_FIELDS():
    return {
        "recipient_name", "phone_number", "address_line_1", "address_line_2",
        "city", "district", "postal_code", "is_default",
    }


def create_address(customer_id: str, data: dict) -> dict:
    """
    Create a new address for the customer.
    If is_default=True, clears the existing default first.
    Returns the created row.
    Raises CustomerError on failure.
    """
    safe = {k: v for k, v in data.items() if k in _EDITABLE_ADDRESS_FIELDS()}
    safe["customer_id"] = customer_id  # always set from server

    # Validate required fields
    for req in ("recipient_name", "address_line_1", "city"):
        if not safe.get(req, "").strip():
            raise CustomerError(f"{req.replace('_', ' ').title()} is required.")

    if safe.get("is_default"):
        _clear_default_address(customer_id)

    try:
        client = get_supabase_client()
        resp = client.table(ADDRESSES_TABLE).insert(safe).execute()
        rows = resp.data or []
        if not rows:
            raise CustomerError("Address creation returned no data.")
        return rows[0]
    except CustomerError:
        raise
    except APIError as exc:
        logger.error("create_address failed for %s: %s", customer_id, exc)
        raise CustomerError("Could not save address. Please try again.") from exc


def update_address(customer_id: str, address_id: str, data: dict) -> dict:
    """
    Update an address that belongs to customer_id.
    Raises CustomerError if not found/owned, or on DB failure.
    """
    # Verify ownership first
    existing = get_address_by_id(customer_id, address_id)
    if not existing:
        raise CustomerError("Address not found.")

    safe = {k: v for k, v in data.items() if k in _EDITABLE_ADDRESS_FIELDS()}
    if not safe:
        raise CustomerError("No updatable fields provided.")

    if safe.get("is_default"):
        _clear_default_address(customer_id, exclude_id=address_id)

    try:
        client = get_supabase_client()
        resp = (
            client.table(ADDRESSES_TABLE)
            .update(safe)
            .eq("address_id", address_id)
            .eq("customer_id", customer_id)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise CustomerError("Address update returned no data.")
        return rows[0]
    except CustomerError:
        raise
    except APIError as exc:
        logger.error("update_address failed for %s/%s: %s", customer_id, address_id, exc)
        raise CustomerError("Could not update address. Please try again.") from exc


def delete_address(customer_id: str, address_id: str) -> None:
    """Delete an address owned by customer_id. Raises CustomerError if not found."""
    existing = get_address_by_id(customer_id, address_id)
    if not existing:
        raise CustomerError("Address not found.")
    try:
        client = get_supabase_client()
        client.table(ADDRESSES_TABLE).delete().eq(
            "address_id", address_id
        ).eq("customer_id", customer_id).execute()
    except APIError as exc:
        logger.error("delete_address failed for %s/%s: %s", customer_id, address_id, exc)
        raise CustomerError("Could not delete address. Please try again.") from exc


def set_default_address(customer_id: str, address_id: str) -> None:
    """Set one address as the default, clearing any existing default."""
    existing = get_address_by_id(customer_id, address_id)
    if not existing:
        raise CustomerError("Address not found.")
    _clear_default_address(customer_id, exclude_id=address_id)
    try:
        client = get_supabase_client()
        client.table(ADDRESSES_TABLE).update({"is_default": True}).eq(
            "address_id", address_id
        ).eq("customer_id", customer_id).execute()
    except APIError as exc:
        logger.error("set_default_address failed for %s/%s: %s", customer_id, address_id, exc)
        raise CustomerError("Could not set default address.") from exc


def _clear_default_address(customer_id: str, exclude_id: Optional[str] = None) -> None:
    """Unset is_default for all addresses of this customer, optionally skipping one."""
    try:
        client = get_supabase_client()
        q = (
            client.table(ADDRESSES_TABLE)
            .update({"is_default": False})
            .eq("customer_id", customer_id)
            .eq("is_default", True)
        )
        if exclude_id:
            q = q.neq("address_id", exclude_id)
        q.execute()
    except Exception as exc:
        logger.warning("_clear_default_address failed for %s: %s", customer_id, exc)


# ── Guest-to-customer cart merge ──────────────────────────────────────────────

def merge_guest_cart_into_customer_cart(
    session,
    customer_id: str,
) -> dict:
    """
    Transfer guest cart items into the authenticated customer's owned cart.

    Algorithm:
    1. Read the guest cart reference from the current session (SESSION_KEY).
    2. Find/create the owned cart (customer_id IS NOT NULL).
    3. For each guest item: validate product, sum with existing quantity, cap at stock.
       Skip inactive/out-of-stock products; collect skip reasons.
    4. If all writes succeed: delete guest cart_items, delete guest carts row,
       clear the guest-cart session key.
    5. Return a summary dict for the caller to show to the user.

    Returns:
      {
        "merged": int,       # number of product lines successfully merged
        "skipped": list[str], # messages for items that could not be carried forward
        "error": str|None,   # non-None if the merge itself failed (retryable)
      }

    Raises MergeError only on catastrophic/unrecoverable failures.
    Does NOT raise for individual item skips.
    """
    from cart.services import SESSION_KEY, CARTS_TABLE, CART_ITEMS_TABLE, PRODUCTS_TABLE

    guest_cart_ref = session.get(SESSION_KEY)
    if not guest_cart_ref:
        # No guest cart — nothing to merge
        return {"merged": 0, "skipped": [], "error": None}

    client = get_supabase_client()

    # ── 1. Find guest cart row ────────────────────────────────────────────────
    try:
        gc_resp = (
            client.table(CARTS_TABLE)
            .select("cart_id")
            .eq("session_id", guest_cart_ref)
            .limit(1)
            .execute()
        )
        guest_cart_rows = gc_resp.data or []
    except APIError as exc:
        logger.error("merge: guest cart lookup failed: %s", exc)
        return {"merged": 0, "skipped": [], "error": "Could not read your guest cart. Please try again."}

    if not guest_cart_rows:
        # Guest cart row does not exist in DB yet — nothing to merge
        from cart.services import clear_cart_session
        clear_cart_session(session)
        return {"merged": 0, "skipped": [], "error": None}

    guest_cart_id = guest_cart_rows[0]["cart_id"]

    # ── 2. Read guest cart items ──────────────────────────────────────────────
    try:
        items_resp = (
            client.table(CART_ITEMS_TABLE)
            .select("cart_item_id, product_id, quantity")
            .eq("cart_id", guest_cart_id)
            .execute()
        )
        guest_items = items_resp.data or []
    except APIError as exc:
        logger.error("merge: guest cart items fetch failed: %s", exc)
        return {"merged": 0, "skipped": [], "error": "Could not read your guest cart items. Please try again."}

    if not guest_items:
        # Empty guest cart — clean up and return
        _cleanup_guest_cart(client, guest_cart_id, session)
        return {"merged": 0, "skipped": [], "error": None}

    # ── 3. Find or create the customer's owned cart ───────────────────────────
    try:
        oc_resp = (
            client.table(CARTS_TABLE)
            .select("cart_id")
            .eq("customer_id", customer_id)
            .limit(1)
            .execute()
        )
        oc_rows = oc_resp.data or []
        if oc_rows:
            owned_cart_id = oc_rows[0]["cart_id"]
        else:
            create_resp = (
                client.table(CARTS_TABLE)
                .insert({"customer_id": customer_id})
                .execute()
            )
            created = create_resp.data or []
            if not created:
                raise APIError({"message": "Owned cart creation returned no data."})
            owned_cart_id = created[0]["cart_id"]
    except APIError as exc:
        logger.error("merge: owned cart find/create failed for %s: %s", customer_id, exc)
        return {"merged": 0, "skipped": [], "error": "Could not access your account cart. Please try again."}

    # ── 4. Read existing owned cart items ─────────────────────────────────────
    try:
        existing_resp = (
            client.table(CART_ITEMS_TABLE)
            .select("cart_item_id, product_id, quantity")
            .eq("cart_id", owned_cart_id)
            .execute()
        )
        existing_items = {row["product_id"]: row for row in (existing_resp.data or [])}
    except APIError as exc:
        logger.error("merge: owned cart items fetch failed: %s", exc)
        return {"merged": 0, "skipped": [], "error": "Could not read your account cart. Please try again."}

    # ── 5. Merge each guest item ──────────────────────────────────────────────
    merged = 0
    skipped = []

    for item in guest_items:
        product_id = item["product_id"]
        guest_qty = int(item["quantity"])

        # Validate product (active, in stock)
        try:
            prod_resp = (
                client.table(PRODUCTS_TABLE)
                .select("product_id, name, stock_quantity, is_active, categories!inner(is_active)")
                .eq("product_id", product_id)
                .limit(1)
                .execute()
            )
            prod_rows = prod_resp.data or []
        except APIError:
            skipped.append(f"An item could not be verified and was skipped.")
            continue

        if not prod_rows:
            skipped.append(f"A product is no longer available and was not transferred.")
            continue

        prod = prod_rows[0]
        if not prod.get("is_active") or not (prod.get("categories") or {}).get("is_active", False):
            skipped.append(f"'{prod.get('name', 'A product')}' is no longer available and was not transferred.")
            continue

        stock = int(prod.get("stock_quantity", 0))
        if stock <= 0:
            skipped.append(f"'{prod.get('name', 'A product')}' is out of stock and was not transferred.")
            continue

        # Determine final quantity
        existing = existing_items.get(product_id)
        current_qty = int(existing["quantity"]) if existing else 0
        new_qty = min(current_qty + guest_qty, stock)

        if new_qty <= current_qty and current_qty > 0:
            skipped.append(
                f"'{prod.get('name', 'A product')}' was already at maximum quantity in your cart."
            )
            merged += 1  # it is in the cart, just at stock cap
            continue

        # Write to owned cart
        try:
            if existing:
                client.table(CART_ITEMS_TABLE).update(
                    {"quantity": new_qty}
                ).eq("cart_item_id", existing["cart_item_id"]).execute()
            else:
                client.table(CART_ITEMS_TABLE).insert({
                    "cart_id": owned_cart_id,
                    "product_id": product_id,
                    "quantity": new_qty,
                }).execute()
            merged += 1
        except APIError as exc:
            logger.error("merge: write failed for product %s: %s", product_id, exc)
            skipped.append(f"'{prod.get('name', 'A product')}' could not be transferred.")

    # ── 6. Clean up guest cart ────────────────────────────────────────────────
    _cleanup_guest_cart(client, guest_cart_id, session)

    return {"merged": merged, "skipped": skipped, "error": None}


def _cleanup_guest_cart(client, guest_cart_id: str, session) -> None:
    """Delete guest cart items and the cart row; clear the session reference."""
    from cart.services import SESSION_KEY, clear_cart_session
    try:
        client.table("cart_items").delete().eq("cart_id", guest_cart_id).execute()
        client.table("carts").delete().eq("cart_id", guest_cart_id).execute()
    except Exception as exc:
        logger.warning("Guest cart cleanup failed for %s: %s", guest_cart_id, exc)
    clear_cart_session(session)


# ── Context helper ────────────────────────────────────────────────────────────

def get_customer_nav_context(session) -> dict:
    """
    Return minimal profile data for the navigation control.
    Failure-tolerant — returns a guest/empty state on any error.
    """
    customer_id = get_authenticated_customer_id(session)
    if not customer_id:
        return {"customer": None, "customer_authenticated": False}
    try:
        client = get_supabase_client()
        resp = (
            client.table(CUSTOMERS_TABLE)
            .select("customer_id, first_name, last_name, profile_image_url")
            .eq("customer_id", customer_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return {"customer": rows[0], "customer_authenticated": True}
    except Exception as exc:
        logger.debug("get_customer_nav_context failed: %s", exc)
    return {"customer": None, "customer_authenticated": False}
