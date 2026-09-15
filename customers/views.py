"""
Customer account views: registration, login, logout, profile, addresses,
profile image upload/delete.

Security rules:
- All state-changing operations are POST-only with CSRF protection + PRG.
- Session rotation on login/logout.
- The authenticated customer_id from the server-side session is the sole
  authority for profile/address/cart access.
- Never accept a customer_id or address_id as ownership proof from POST body.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .services import (
    AuthError,
    CustomerError,
    CUSTOMER_ID_SESSION_KEY,
    get_authenticated_customer_id,
    is_customer_authenticated,
    register_customer,
    login_customer,
    logout_customer,
    get_customer_profile,
    update_customer_profile,
    upload_profile_image,
    delete_profile_image,
    get_customer_addresses,
    get_address_by_id,
    create_address,
    update_address,
    delete_address,
    set_default_address,
    merge_guest_cart_into_customer_cart,
    _safe_next,
)

logger = logging.getLogger(__name__)

# ── Auth decorator ────────────────────────────────────────────────────────────

def _require_customer(view_func):
    """Redirect unauthenticated customers to the login page."""
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_customer_authenticated(request.session):
            next_url = request.get_full_path()
            return redirect(f"/account/login/?next={next_url}")
        return view_func(request, *args, **kwargs)

    return _wrapped


# ── Registration ──────────────────────────────────────────────────────────────

def customer_register(request):
    """GET: show registration page. POST: create account and session."""
    if is_customer_authenticated(request.session):
        return redirect("customers:profile")

    next_url = _safe_next(request.GET.get("next", "") or request.POST.get("next", ""))

    if request.method == "POST":
        try:
            result = register_customer(
                session=request.session,
                first_name=request.POST.get("first_name", "").strip(),
                last_name=request.POST.get("last_name", "").strip(),
                email=request.POST.get("email", "").strip(),
                password=request.POST.get("password", ""),
                password_confirm=request.POST.get("password_confirm", ""),
                phone_number=request.POST.get("phone_number", ""),
                is_email_notifications_enabled=request.POST.get("email_notifications") == "1",
                is_sms_notifications_enabled=request.POST.get("sms_notifications") == "1",
            )
        except AuthError as exc:
            messages.error(request, str(exc))
            return render(request, "customers/register.html", {
                "form_data": request.POST,
                "next": next_url,
            })

        if result["email_confirmation_required"]:
            return redirect("customers:confirm_email")

        if result["session_established"]:
            # Merge guest cart
            _perform_cart_merge(request)
            request.session.cycle_key()
            messages.success(request, f"Welcome, {result['customer']['first_name']}! Your account is ready.")
            return redirect(next_url or "customers:profile")

        # Auth succeeded but session not established — ask them to log in
        messages.success(request, "Account created. Please log in.")
        return redirect("customers:login")

    return render(request, "customers/register.html", {
        "form_data": {},
        "next": next_url,
    })


# ── Login ─────────────────────────────────────────────────────────────────────

def customer_login(request):
    """GET: show login page. POST: authenticate and establish session."""
    if is_customer_authenticated(request.session):
        return redirect("customers:profile")

    next_url = _safe_next(request.GET.get("next", "") or request.POST.get("next", ""))

    if request.method == "POST":
        try:
            customer = login_customer(
                session=request.session,
                email=request.POST.get("email", "").strip(),
                password=request.POST.get("password", ""),
            )
        except AuthError as exc:
            messages.error(request, str(exc))
            return render(request, "customers/login.html", {
                "email": request.POST.get("email", ""),
                "next": next_url,
            })

        # Merge guest cart then rotate session
        _perform_cart_merge(request)
        request.session.cycle_key()
        messages.success(request, f"Welcome back, {customer['first_name']}!")
        return redirect(next_url or "customers:profile")

    return render(request, "customers/login.html", {
        "email": "",
        "next": next_url,
    })


# ── Logout ────────────────────────────────────────────────────────────────────

@require_POST
def customer_logout(request):
    """POST-only: sign out the customer and rotate/flush the session."""
    logout_customer(request.session)
    request.session.flush()  # rotate session, clear all state
    messages.success(request, "You have been signed out.")
    return redirect("cakes:catalogue")


# ── Cart merge helper ─────────────────────────────────────────────────────────

def _perform_cart_merge(request):
    """
    Attempt guest→customer cart merge after successful authentication.
    Shows flash messages for skipped items. Never raises.
    """
    customer_id = get_authenticated_customer_id(request.session)
    if not customer_id:
        return
    try:
        result = merge_guest_cart_into_customer_cart(request.session, customer_id)
        if result.get("error"):
            messages.warning(request, result["error"])
            return
        for msg in result.get("skipped", []):
            messages.warning(request, msg)
        if result.get("merged", 0) > 0 and not result.get("skipped"):
            messages.info(request, "Your previous cart items have been added to your account.")
    except Exception as exc:
        logger.error("Cart merge failed: %s", exc)
        messages.warning(request, "Your guest cart could not be merged. Please add items again.")


# ── Confirm email ─────────────────────────────────────────────────────────────

def confirm_email(request):
    """Show a 'check your email' message after registration with email confirmation."""
    return render(request, "customers/confirm_email.html")


# ── Profile ───────────────────────────────────────────────────────────────────

@_require_customer
def profile(request):
    """Display the authenticated customer's profile."""
    customer_id = get_authenticated_customer_id(request.session)
    customer = get_customer_profile(customer_id)
    if not customer:
        messages.error(request, "Could not load your profile. Please try again.")
        return render(request, "customers/profile.html", {"customer": None})

    addresses = get_customer_addresses(customer_id)
    return render(request, "customers/profile.html", {
        "customer": customer,
        "addresses": addresses,
    })


@_require_customer
def profile_edit(request):
    """GET: show edit form. POST: update profile fields."""
    customer_id = get_authenticated_customer_id(request.session)
    customer = get_customer_profile(customer_id)
    if not customer:
        raise Http404("Profile not found.")

    if request.method == "POST":
        data = {
            "first_name": request.POST.get("first_name", "").strip(),
            "last_name": request.POST.get("last_name", "").strip(),
            "phone_number": request.POST.get("phone_number", "").strip() or None,
            "date_of_birth": request.POST.get("date_of_birth", "").strip() or None,
            "is_email_notifications_enabled": request.POST.get("email_notifications") == "1",
            "is_sms_notifications_enabled": request.POST.get("sms_notifications") == "1",
        }
        try:
            from .services import _validate_name, _validate_date_of_birth
            _validate_name(data["first_name"], "First name")
            _validate_name(data["last_name"], "Last name")
            if data["date_of_birth"]:
                data["date_of_birth"] = _validate_date_of_birth(data["date_of_birth"])
            update_customer_profile(customer_id, data)
            messages.success(request, "Profile updated.")
            return redirect("customers:profile")
        except (CustomerError, AuthError) as exc:
            messages.error(request, str(exc))
            return render(request, "customers/profile_edit.html", {
                "customer": customer,
                "form_data": request.POST,
            })

    return render(request, "customers/profile_edit.html", {
        "customer": customer,
        "form_data": customer,
    })


# ── Profile image ─────────────────────────────────────────────────────────────

@require_POST
@_require_customer
def profile_image_upload(request):
    """Upload and save a new profile image."""
    customer_id = get_authenticated_customer_id(request.session)
    uploaded = request.FILES.get("profile_image")
    if not uploaded:
        messages.error(request, "No file was selected.")
        return redirect("customers:profile")

    try:
        file_bytes = uploaded.read()
        upload_profile_image(customer_id, file_bytes, uploaded.content_type or "")
        messages.success(request, "Profile photo updated.")
    except CustomerError as exc:
        messages.error(request, str(exc))

    return redirect("customers:profile")


@require_POST
@_require_customer
def profile_image_delete(request):
    """Remove the profile image."""
    customer_id = get_authenticated_customer_id(request.session)
    try:
        delete_profile_image(customer_id)
        messages.success(request, "Profile photo removed.")
    except CustomerError as exc:
        messages.error(request, str(exc))
    return redirect("customers:profile")


# ── Addresses ─────────────────────────────────────────────────────────────────

@_require_customer
def address_list(request):
    """List all customer addresses."""
    customer_id = get_authenticated_customer_id(request.session)
    addresses = get_customer_addresses(customer_id)
    return render(request, "customers/address_list.html", {"addresses": addresses})


@_require_customer
def address_create(request):
    """GET: show form. POST: create a new address."""
    if request.method == "POST":
        customer_id = get_authenticated_customer_id(request.session)
        data = _extract_address_form(request.POST)
        try:
            create_address(customer_id, data)
            messages.success(request, "Address saved.")
            return redirect("customers:address_list")
        except CustomerError as exc:
            messages.error(request, str(exc))
            return render(request, "customers/address_form.html", {
                "form_data": request.POST,
                "is_edit": False,
            })

    return render(request, "customers/address_form.html", {
        "form_data": {},
        "is_edit": False,
    })


@_require_customer
def address_edit(request, address_id):
    """GET: show edit form. POST: update existing address."""
    customer_id = get_authenticated_customer_id(request.session)
    address = get_address_by_id(customer_id, str(address_id))
    if not address:
        raise Http404("Address not found.")

    if request.method == "POST":
        data = _extract_address_form(request.POST)
        try:
            update_address(customer_id, str(address_id), data)
            messages.success(request, "Address updated.")
            return redirect("customers:address_list")
        except CustomerError as exc:
            messages.error(request, str(exc))
            return render(request, "customers/address_form.html", {
                "address": address,
                "form_data": request.POST,
                "is_edit": True,
            })

    return render(request, "customers/address_form.html", {
        "address": address,
        "form_data": address,
        "is_edit": True,
    })


@require_POST
@_require_customer
def address_delete(request, address_id):
    """Delete an address owned by the current customer."""
    customer_id = get_authenticated_customer_id(request.session)
    try:
        delete_address(customer_id, str(address_id))
        messages.success(request, "Address deleted.")
    except CustomerError as exc:
        messages.error(request, str(exc))
    return redirect("customers:address_list")


@require_POST
@_require_customer
def address_set_default(request, address_id):
    """Set an address as the default delivery address."""
    customer_id = get_authenticated_customer_id(request.session)
    try:
        set_default_address(customer_id, str(address_id))
        messages.success(request, "Default address updated.")
    except CustomerError as exc:
        messages.error(request, str(exc))
    return redirect("customers:address_list")


def _extract_address_form(post) -> dict:
    """Extract and sanitize address form fields from POST data."""
    return {
        "recipient_name": post.get("recipient_name", "").strip(),
        "phone_number": post.get("phone_number", "").strip() or None,
        "address_line_1": post.get("address_line_1", "").strip(),
        "address_line_2": post.get("address_line_2", "").strip() or None,
        "city": post.get("city", "").strip(),
        "district": post.get("district", "").strip() or None,
        "postal_code": post.get("postal_code", "").strip() or None,
        "is_default": post.get("is_default") == "1",
    }
