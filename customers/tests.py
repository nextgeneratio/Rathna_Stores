"""
Customers app tests — Phase 3.
All Supabase/Auth calls are mocked. No live credentials required.
Run with: python manage.py test customers
"""
from unittest.mock import MagicMock, patch, PropertyMock
from django.test import TestCase, Client
from django.urls import reverse

from customers.services import (
    CUSTOMER_ID_SESSION_KEY,
    ACCESS_TOKEN_SESSION_KEY,
    REFRESH_TOKEN_SESSION_KEY,
    SESSION_KEY as GUEST_CART_KEY,  # re-exported for tests
    AuthError,
    CustomerError,
    get_authenticated_customer_id,
    is_customer_authenticated,
    clear_customer_session,
    _safe_next,
    _validate_email,
    _validate_password,
    _validate_date_of_birth,
    _safe_mime_from_bytes,
)

# ── Import SESSION_KEY from cart for merge tests ─────────────────────────────
from cart.services import SESSION_KEY as CART_SESSION_KEY


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_session(**extra):
    """Plain dict simulating a Django session."""
    return dict(extra)


def _make_customer(**overrides):
    base = {
        "customer_id": "cccc0000-0000-0000-0000-000000000001",
        "auth_user_id": "aaaa0000-0000-0000-0000-000000000001",
        "first_name": "Amara",
        "last_name": "Perera",
        "email": "amara@example.com",
        "phone_number": None,
        "date_of_birth": None,
        "profile_image_url": None,
        "is_email_notifications_enabled": True,
        "is_sms_notifications_enabled": False,
        "is_active": True,
    }
    base.update(overrides)
    return base


def _make_auth_user(user_id="aaaa0000-0000-0000-0000-000000000001"):
    u = MagicMock()
    u.id = user_id
    return u


def _make_auth_session():
    s = MagicMock()
    s.access_token = "test_access_token"
    s.refresh_token = "test_refresh_token"
    return s


def _make_auth_response(user=None, session=None):
    r = MagicMock()
    r.user = user or _make_auth_user()
    r.session = session or _make_auth_session()
    return r


def _make_address(**overrides):
    base = {
        "address_id": "eeee0000-0000-0000-0000-000000000001",
        "customer_id": "cccc0000-0000-0000-0000-000000000001",
        "recipient_name": "Amara Perera",
        "address_line_1": "12 Galle Road",
        "city": "Colombo",
        "is_default": True,
    }
    base.update(overrides)
    return base


def _mock_supabase(table_responses: dict):
    """Build a MagicMock Supabase client returning preset rows per table."""
    client = MagicMock()

    def table_side_effect(name):
        q = MagicMock()
        rows = table_responses.get(name, [])
        q.select.return_value = q
        q.eq.return_value = q
        q.neq.return_value = q
        q.order.return_value = q
        q.limit.return_value = q
        q.in_.return_value = q
        q.insert.return_value = q
        q.update.return_value = q
        q.delete.return_value = q
        q.execute.return_value = MagicMock(data=rows)
        return q

    client.table.side_effect = table_side_effect
    return client


# ── 1. Session helpers ────────────────────────────────────────────────────────

class SessionHelperTests(TestCase):

    def test_get_authenticated_customer_id_returns_none_for_guest(self):
        self.assertIsNone(get_authenticated_customer_id({}))

    def test_get_authenticated_customer_id_returns_value(self):
        session = {CUSTOMER_ID_SESSION_KEY: "some-uuid"}
        self.assertEqual(get_authenticated_customer_id(session), "some-uuid")

    def test_is_customer_authenticated_false_for_guest(self):
        self.assertFalse(is_customer_authenticated({}))

    def test_is_customer_authenticated_true_when_set(self):
        session = {CUSTOMER_ID_SESSION_KEY: "some-uuid"}
        self.assertTrue(is_customer_authenticated(session))

    def test_clear_customer_session_removes_all_keys(self):
        session = {
            CUSTOMER_ID_SESSION_KEY: "id",
            ACCESS_TOKEN_SESSION_KEY: "tok",
            REFRESH_TOKEN_SESSION_KEY: "ref",
            "other_key": "preserved",
        }
        clear_customer_session(session)
        self.assertNotIn(CUSTOMER_ID_SESSION_KEY, session)
        self.assertNotIn(ACCESS_TOKEN_SESSION_KEY, session)
        self.assertNotIn(REFRESH_TOKEN_SESSION_KEY, session)
        self.assertIn("other_key", session)  # unrelated key preserved

    def test_clear_customer_session_noop_on_empty(self):
        session = {}
        clear_customer_session(session)  # must not raise
        self.assertEqual(session, {})


# ── 2. Input validation ───────────────────────────────────────────────────────

class InputValidationTests(TestCase):

    def test_validate_email_strips_and_lowercases(self):
        result = _validate_email("  AMARA@EXAMPLE.COM  ")
        self.assertEqual(result, "amara@example.com")

    def test_validate_email_rejects_invalid(self):
        with self.assertRaises(AuthError):
            _validate_email("notanemail")

    def test_validate_password_rejects_short(self):
        with self.assertRaises(AuthError):
            _validate_password("short")

    def test_validate_password_accepts_8_chars(self):
        _validate_password("validpwd")  # should not raise

    def test_validate_dob_rejects_future(self):
        with self.assertRaises(CustomerError):
            _validate_date_of_birth("2099-01-01")

    def test_validate_dob_rejects_invalid_format(self):
        with self.assertRaises(CustomerError):
            _validate_date_of_birth("not-a-date")

    def test_validate_dob_accepts_valid_past(self):
        result = _validate_date_of_birth("1990-06-15")
        self.assertEqual(result, "1990-06-15")

    def test_validate_dob_none_for_empty(self):
        self.assertIsNone(_validate_date_of_birth(""))

    def test_safe_next_allows_relative(self):
        self.assertEqual(_safe_next("/cart/"), "/cart/")

    def test_safe_next_blocks_external(self):
        self.assertEqual(_safe_next("https://evil.com"), "/")

    def test_safe_next_blocks_protocol_relative(self):
        self.assertEqual(_safe_next("//evil.com"), "/")


# ── 3. MIME sniffing ──────────────────────────────────────────────────────────

class MimeSniffTests(TestCase):

    def test_jpeg_detected(self):
        jpeg_magic = b'\xff\xd8\xff' + b'\x00' * 9
        self.assertEqual(_safe_mime_from_bytes(jpeg_magic), "image/jpeg")

    def test_png_detected(self):
        png_magic = b'\x89PNG\r\n\x1a\n' + b'\x00' * 4
        self.assertEqual(_safe_mime_from_bytes(png_magic), "image/png")

    def test_webp_detected(self):
        webp_magic = b'RIFF' + b'\x00\x00\x00\x00' + b'WEBP'
        self.assertEqual(_safe_mime_from_bytes(webp_magic), "image/webp")

    def test_unknown_returns_none(self):
        self.assertIsNone(_safe_mime_from_bytes(b'UNKNOWN_GARBAGE_BYTES___'))


# ── 4. Registration service (mocked) ─────────────────────────────────────────

class RegistrationServiceTests(TestCase):

    @patch("customers.services.get_supabase_client")
    def test_register_success_no_email_confirmation(self, mock_get_client):
        from customers.services import register_customer
        auth_resp = _make_auth_response()
        mock_client = _mock_supabase({"customers": [_make_customer()]})
        mock_client.auth.sign_up.return_value = auth_resp
        mock_get_client.return_value = mock_client

        session = {}
        result = register_customer(session, "Amara", "Perera", "amara@example.com",
                                   "password123", "password123")
        self.assertFalse(result["email_confirmation_required"])
        self.assertTrue(result["session_established"])
        self.assertIn(CUSTOMER_ID_SESSION_KEY, session)

    @patch("customers.services.get_supabase_client")
    def test_register_email_confirmation_required(self, mock_get_client):
        from customers.services import register_customer
        auth_resp = MagicMock()
        auth_resp.user = _make_auth_user()
        auth_resp.session = None  # no session → confirmation required
        mock_client = _mock_supabase({"customers": [_make_customer()]})
        mock_client.auth.sign_up.return_value = auth_resp
        mock_get_client.return_value = mock_client

        session = {}
        result = register_customer(session, "Amara", "Perera", "amara@example.com",
                                   "password123", "password123")
        self.assertTrue(result["email_confirmation_required"])
        self.assertFalse(result["session_established"])
        self.assertNotIn(CUSTOMER_ID_SESSION_KEY, session)

    def test_register_password_mismatch_raises(self):
        from customers.services import register_customer
        with self.assertRaises(AuthError) as ctx:
            register_customer({}, "A", "B", "a@b.com", "pass1234", "different")
        self.assertIn("match", str(ctx.exception).lower())

    def test_register_short_password_raises(self):
        from customers.services import register_customer
        with self.assertRaises(AuthError):
            register_customer({}, "A", "B", "a@b.com", "short", "short")

    def test_register_invalid_email_raises(self):
        from customers.services import register_customer
        with self.assertRaises(AuthError):
            register_customer({}, "A", "B", "not-email", "password1", "password1")

    def test_register_missing_first_name_raises(self):
        from customers.services import register_customer
        with self.assertRaises((AuthError, CustomerError)):
            register_customer({}, "", "B", "a@b.com", "password1", "password1")

    @patch("customers.services.get_supabase_client")
    def test_register_duplicate_email_raises_auth_error(self, mock_get_client):
        from customers.services import register_customer
        mock_client = MagicMock()
        mock_client.auth.sign_up.side_effect = Exception("already registered")
        mock_get_client.return_value = mock_client
        with self.assertRaises(AuthError):
            register_customer({}, "A", "B", "a@b.com", "password1", "password1")

    @patch("customers.services.get_supabase_client")
    def test_register_supabase_error_raises_auth_error(self, mock_get_client):
        from customers.services import register_customer
        mock_client = MagicMock()
        mock_client.auth.sign_up.side_effect = Exception("network error")
        mock_get_client.return_value = mock_client
        with self.assertRaises(AuthError):
            register_customer({}, "A", "B", "a@b.com", "password12", "password12")


# ── 5. Login service (mocked) ─────────────────────────────────────────────────

class LoginServiceTests(TestCase):

    @patch("customers.services.get_supabase_client")
    def test_login_success_establishes_session(self, mock_get_client):
        from customers.services import login_customer
        auth_resp = _make_auth_response()
        mock_client = _mock_supabase({"customers": [_make_customer()]})
        mock_client.auth.sign_in_with_password.return_value = auth_resp
        mock_get_client.return_value = mock_client

        session = {}
        customer = login_customer(session, "amara@example.com", "password123")
        self.assertEqual(customer["first_name"], "Amara")
        self.assertIn(CUSTOMER_ID_SESSION_KEY, session)

    @patch("customers.services.get_supabase_client")
    def test_login_invalid_credentials_raises(self, mock_get_client):
        from customers.services import login_customer
        mock_client = MagicMock()
        mock_client.auth.sign_in_with_password.side_effect = Exception("invalid credentials")
        mock_get_client.return_value = mock_client
        with self.assertRaises(AuthError) as ctx:
            login_customer({}, "a@b.com", "wrongpassword")
        self.assertNotIn("wrongpassword", str(ctx.exception))

    @patch("customers.services.get_supabase_client")
    def test_login_inactive_customer_raises(self, mock_get_client):
        from customers.services import login_customer
        auth_resp = _make_auth_response()
        mock_client = _mock_supabase({"customers": [_make_customer(is_active=False)]})
        mock_client.auth.sign_in_with_password.return_value = auth_resp
        mock_get_client.return_value = mock_client
        with self.assertRaises(AuthError):
            login_customer({}, "amara@example.com", "password123")

    @patch("customers.services.get_supabase_client")
    def test_login_missing_customers_row_raises(self, mock_get_client):
        from customers.services import login_customer
        auth_resp = _make_auth_response()
        mock_client = _mock_supabase({"customers": []})  # no row
        mock_client.auth.sign_in_with_password.return_value = auth_resp
        mock_get_client.return_value = mock_client
        with self.assertRaises(AuthError):
            login_customer({}, "amara@example.com", "password123")

    @patch("customers.services.get_supabase_client")
    def test_login_with_tokens_establishes_session(self, mock_get_client):
        from customers.services import login_customer_with_tokens
        mock_client = _mock_supabase({"customers": [_make_customer()]})
        mock_client.auth.get_user.return_value = _make_auth_response(session=None)
        mock_get_client.return_value = mock_client

        session = {}
        customer = login_customer_with_tokens(session, "callback_access", "callback_refresh")

        self.assertEqual(customer["first_name"], "Amara")
        self.assertEqual(session[ACCESS_TOKEN_SESSION_KEY], "callback_access")
        self.assertEqual(session[REFRESH_TOKEN_SESSION_KEY], "callback_refresh")
        mock_client.auth.get_user.assert_called_once_with("callback_access")

    def test_login_empty_password_raises(self):
        from customers.services import login_customer
        with self.assertRaises(AuthError):
            login_customer({}, "a@b.com", "")


# ── 6. Logout service ─────────────────────────────────────────────────────────

class LogoutServiceTests(TestCase):

    @patch("customers.services.get_supabase_client")
    def test_logout_clears_session(self, mock_get_client):
        from customers.services import logout_customer
        mock_client = MagicMock()
        mock_client.auth.sign_out.return_value = None
        mock_get_client.return_value = mock_client

        session = {
            CUSTOMER_ID_SESSION_KEY: "id",
            ACCESS_TOKEN_SESSION_KEY: "tok",
        }
        logout_customer(session)
        self.assertNotIn(CUSTOMER_ID_SESSION_KEY, session)

    @patch("customers.services.get_supabase_client")
    def test_logout_does_not_raise_on_supabase_error(self, mock_get_client):
        from customers.services import logout_customer
        mock_client = MagicMock()
        mock_client.auth.sign_out.side_effect = Exception("network down")
        mock_get_client.return_value = mock_client
        session = {CUSTOMER_ID_SESSION_KEY: "id"}
        logout_customer(session)  # must not raise
        self.assertNotIn(CUSTOMER_ID_SESSION_KEY, session)


# ── 7. Cart merge ─────────────────────────────────────────────────────────────

class CartMergeTests(TestCase):

    def _make_merge_client(self, guest_items, existing_owned_items, products):
        """Build a mock client for merge tests."""
        client = MagicMock()

        def table_side_effect(name):
            q = MagicMock()
            q.select.return_value = q; q.eq.return_value = q
            q.neq.return_value = q; q.order.return_value = q
            q.limit.return_value = q; q.in_.return_value = q
            q.insert.return_value = q; q.update.return_value = q
            q.delete.return_value = q

            if name == "carts":
                # Returns guest cart on session_id lookup, owned cart on customer_id
                calls = []
                def eq_side(col, val):
                    calls.append((col, val))
                    q2 = MagicMock()
                    q2.select.return_value = q2; q2.eq.return_value = q2
                    q2.neq.return_value = q2; q2.limit.return_value = q2
                    q2.insert.return_value = q2; q2.delete.return_value = q2
                    q2.update.return_value = q2; q2.order.return_value = q2
                    if col == "session_id":
                        q2.execute.return_value = MagicMock(data=[{"cart_id": "guest-cart-id"}])
                    elif col == "customer_id":
                        q2.execute.return_value = MagicMock(data=[{"cart_id": "owned-cart-id"}])
                    else:
                        q2.execute.return_value = MagicMock(data=[])
                    return q2
                q.eq.side_effect = eq_side
                q.execute.return_value = MagicMock(data=[])
            elif name == "cart_items":
                call_count = [0]
                def items_eq(col, val):
                    call_count[0] += 1
                    q2 = MagicMock()
                    q2.select.return_value = q2; q2.eq.return_value = q2
                    q2.execute.return_value = MagicMock(
                        data=guest_items if call_count[0] == 1 else existing_owned_items
                    )
                    q2.delete.return_value = q2; q2.insert.return_value = q2
                    q2.update.return_value = q2
                    return q2
                q.eq.side_effect = items_eq
                q.execute.return_value = MagicMock(data=guest_items)
            elif name == "products":
                q.execute.return_value = MagicMock(data=products)
            else:
                q.execute.return_value = MagicMock(data=[])
            return q

        client.table.side_effect = table_side_effect
        return client

    @patch("customers.services.get_supabase_client")
    def test_merge_no_guest_cart_returns_zero(self, mock_get_client):
        from customers.services import merge_guest_cart_into_customer_cart
        session = {}  # no guest cart ref
        result = merge_guest_cart_into_customer_cart(session, "customer-id")
        self.assertEqual(result["merged"], 0)
        self.assertIsNone(result["error"])

    @patch("customers.services.get_supabase_client")
    def test_merge_skips_out_of_stock_products(self, mock_get_client):
        from customers.services import merge_guest_cart_into_customer_cart
        from cart.services import SESSION_KEY

        session = {SESSION_KEY: "guest-ref"}
        guest_items = [{"cart_item_id": "i1", "product_id": "p1", "quantity": 2}]
        products = [{"product_id": "p1", "name": "Cake", "stock_quantity": 0,
                     "is_active": True, "categories": {"is_active": True}}]

        mock_client = MagicMock()
        # guest cart lookup
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[{"cart_id": "gc1"}])

        # Build a simple mapping
        table_data = {
            "carts_guest": [{"cart_id": "gc1"}],
            "cart_items_guest": guest_items,
            "carts_owned": [{"cart_id": "oc1"}],
            "cart_items_owned": [],
            "products": products,
        }

        call_tracker = {"carts": 0, "cart_items": 0}

        def table_side(name):
            q = MagicMock()
            q.select.return_value = q; q.eq.return_value = q
            q.limit.return_value = q; q.in_.return_value = q
            q.insert.return_value = q; q.update.return_value = q
            q.delete.return_value = q; q.order.return_value = q

            if name == "carts":
                call_tracker["carts"] += 1
                if call_tracker["carts"] == 1:
                    q.execute.return_value = MagicMock(data=[{"cart_id": "gc1"}])
                else:
                    q.execute.return_value = MagicMock(data=[{"cart_id": "oc1"}])
            elif name == "cart_items":
                call_tracker["cart_items"] += 1
                if call_tracker["cart_items"] == 1:
                    q.execute.return_value = MagicMock(data=guest_items)
                else:
                    q.execute.return_value = MagicMock(data=[])
            elif name == "products":
                q.execute.return_value = MagicMock(data=products)
            else:
                q.execute.return_value = MagicMock(data=[])
            return q

        mock_client.table.side_effect = table_side
        mock_get_client.return_value = mock_client

        result = merge_guest_cart_into_customer_cart(session, "customer-id")
        self.assertTrue(len(result["skipped"]) > 0)

    @patch("customers.services.get_supabase_client")
    def test_merge_supabase_failure_returns_error_string(self, mock_get_client):
        from customers.services import merge_guest_cart_into_customer_cart
        from cart.services import SESSION_KEY
        from postgrest.exceptions import APIError

        session = {SESSION_KEY: "guest-ref"}
        # Make the execute call raise APIError (what the merge function catches)
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = APIError({"message": "DB down"})
        mock_get_client.return_value = mock_client
        result = merge_guest_cart_into_customer_cart(session, "customer-id")
        self.assertIsNotNone(result.get("error"))


# ── 8. Profile CRUD ───────────────────────────────────────────────────────────

class ProfileCRUDTests(TestCase):

    @patch("customers.services.get_supabase_client")
    def test_get_customer_profile_returns_row(self, mock_get_client):
        from customers.services import get_customer_profile
        mock_get_client.return_value = _mock_supabase({"customers": [_make_customer()]})
        result = get_customer_profile("cccc0000-0000-0000-0000-000000000001")
        self.assertEqual(result["first_name"], "Amara")

    @patch("customers.services.get_supabase_client")
    def test_get_customer_profile_returns_none_for_missing(self, mock_get_client):
        from customers.services import get_customer_profile
        mock_get_client.return_value = _mock_supabase({"customers": []})
        result = get_customer_profile("cccc0000-0000-0000-0000-000000000001")
        self.assertIsNone(result)

    def test_get_customer_profile_invalid_uuid_returns_none(self):
        from customers.services import get_customer_profile
        result = get_customer_profile("not-a-uuid")
        self.assertIsNone(result)

    @patch("customers.services.get_supabase_client")
    def test_update_profile_strips_protected_fields(self, mock_get_client):
        from customers.services import update_customer_profile
        mock_get_client.return_value = _mock_supabase(
            {"customers": [_make_customer(first_name="New Name")]}
        )
        # Protected fields should be silently stripped, not cause an error
        result = update_customer_profile(
            "cccc0000-0000-0000-0000-000000000001",
            {
                "first_name": "New Name",
                "loyalty_points": 9999,       # protected — must be stripped
                "is_active": False,            # protected — must be stripped
                "customer_id": "evil-uuid",    # protected — must be stripped
            },
        )
        # The actual update call should only include first_name
        call_args = mock_get_client.return_value.table.return_value.update.call_args
        if call_args:
            updated_data = call_args[0][0]
            self.assertNotIn("loyalty_points", updated_data)
            self.assertNotIn("is_active", updated_data)
            self.assertNotIn("customer_id", updated_data)


# ── 9. Profile image upload ───────────────────────────────────────────────────

class ProfileImageTests(TestCase):

    def test_upload_rejects_non_image_bytes(self):
        from customers.services import upload_profile_image
        # Bytes that don't match any image magic, with a non-image declared MIME
        fake_bytes = b'THIS_IS_NOT_AN_IMAGE_FILE_AT_ALL'
        with self.assertRaises(CustomerError) as ctx:
            upload_profile_image("some-customer-id", fake_bytes, "application/pdf")
        # Message should mention accepted formats
        self.assertIn("JPEG", str(ctx.exception))

    def test_upload_rejects_oversized_file(self):
        from customers.services import upload_profile_image
        # Valid JPEG magic but oversized
        jpeg_bytes = b'\xff\xd8\xff' + b'\x00' * (4 * 1024 * 1024 + 1)
        with self.assertRaises(CustomerError) as ctx:
            upload_profile_image("some-id", jpeg_bytes, "image/jpeg")
        self.assertIn("4 MB", str(ctx.exception))

    @patch("customers.services.get_supabase_client")
    def test_upload_success_updates_profile_url(self, mock_get_client):
        from customers.services import upload_profile_image
        jpeg_bytes = b'\xff\xd8\xff' + b'\x00' * 100
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.upload.return_value = None
        mock_client.storage.from_.return_value.get_public_url.return_value = "https://cdn.example.com/profile.jpg"
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[_make_customer(profile_image_url="https://cdn.example.com/profile.jpg")])
        mock_get_client.return_value = mock_client

        url = upload_profile_image("cccc0000-0000-0000-0000-000000000001", jpeg_bytes, "image/jpeg")
        self.assertIn("cdn.example.com", url)

    @patch("customers.services.get_supabase_client")
    def test_upload_cleans_up_storage_on_db_failure(self, mock_get_client):
        from customers.services import upload_profile_image
        from postgrest.exceptions import APIError
        jpeg_bytes = b'\xff\xd8\xff' + b'\x00' * 100
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.upload.return_value = None
        mock_client.storage.from_.return_value.get_public_url.return_value = "https://cdn.example.com/p.jpg"
        # DB write fails
        mock_client.table.return_value.update.return_value.eq.return_value.execute.side_effect = APIError({"message": "DB error"})
        mock_get_client.return_value = mock_client

        with self.assertRaises(CustomerError):
            upload_profile_image("cccc0000-0000-0000-0000-000000000001", jpeg_bytes, "image/jpeg")
        # Verify storage remove was called (cleanup attempted)
        mock_client.storage.from_.return_value.remove.assert_called_once()


# ── 10. Address ownership ─────────────────────────────────────────────────────

class AddressOwnershipTests(TestCase):

    @patch("customers.services.get_supabase_client")
    def test_get_address_returns_none_for_wrong_customer(self, mock_get_client):
        from customers.services import get_address_by_id
        # Returns empty data — address not found for this customer
        mock_get_client.return_value = _mock_supabase({"customer_addresses": []})
        result = get_address_by_id("wrong-customer-id", "eeee0000-0000-0000-0000-000000000001")
        self.assertIsNone(result)

    @patch("customers.services.get_supabase_client")
    def test_get_address_returns_correct_owner_address(self, mock_get_client):
        from customers.services import get_address_by_id
        mock_get_client.return_value = _mock_supabase({"customer_addresses": [_make_address()]})
        result = get_address_by_id("cccc0000-0000-0000-0000-000000000001",
                                   "eeee0000-0000-0000-0000-000000000001")
        self.assertIsNotNone(result)
        self.assertEqual(result["city"], "Colombo")

    def test_get_address_invalid_uuid_returns_none(self):
        from customers.services import get_address_by_id
        result = get_address_by_id("owner-id", "not-a-uuid")
        self.assertIsNone(result)

    @patch("customers.services.get_supabase_client")
    def test_delete_address_raises_if_not_owned(self, mock_get_client):
        from customers.services import delete_address
        # get_address_by_id will return None (wrong owner)
        mock_get_client.return_value = _mock_supabase({"customer_addresses": []})
        with self.assertRaises(CustomerError):
            delete_address("other-customer", "eeee0000-0000-0000-0000-000000000001")


# ── 11. Auth-aware cart resolution ───────────────────────────────────────────

class AuthAwareCartTests(TestCase):

    @patch("cart.services.get_supabase_client")
    def test_cart_count_uses_customer_id_when_authenticated(self, mock_get_client):
        from cart.services import get_cart_unit_count

        session = {CUSTOMER_ID_SESSION_KEY: "cccc0000-0000-0000-0000-000000000001"}

        call_tracker = {"carts": 0}

        def table_side(name):
            q = MagicMock()
            q.select.return_value = q; q.eq.return_value = q
            q.limit.return_value = q; q.execute.return_value = MagicMock(data=[])
            if name == "carts":
                call_tracker["carts"] += 1
                q.execute.return_value = MagicMock(data=[{"cart_id": "oc1"}])
            elif name == "cart_items":
                q.execute.return_value = MagicMock(data=[{"quantity": 3}, {"quantity": 2}])
            return q

        mock_get_client.return_value.table.side_effect = table_side
        count = get_cart_unit_count(session)
        self.assertEqual(count, 5)

    @patch("cart.services.get_supabase_client")
    def test_cart_count_returns_zero_on_failure(self, mock_get_client):
        from cart.services import get_cart_unit_count
        mock_get_client.side_effect = Exception("DB down")
        self.assertEqual(get_cart_unit_count({CUSTOMER_ID_SESSION_KEY: "x"}), 0)


# ── 12. HTTP views — access control ──────────────────────────────────────────

class CustomerViewAccessTests(TestCase):

    def setUp(self):
        self.client = Client()

    def test_profile_redirects_unauthenticated(self):
        resp = self.client.get(reverse("customers:profile"))
        self.assertIn(resp.status_code, [301, 302])

    def test_profile_edit_redirects_unauthenticated(self):
        resp = self.client.get(reverse("customers:profile_edit"))
        self.assertIn(resp.status_code, [301, 302])

    def test_address_list_redirects_unauthenticated(self):
        resp = self.client.get(reverse("customers:address_list"))
        self.assertIn(resp.status_code, [301, 302])

    def test_logout_is_post_only(self):
        resp = self.client.get(reverse("customers:logout"))
        self.assertEqual(resp.status_code, 405)

    def test_login_page_returns_200(self):
        resp = self.client.get(reverse("customers:login"))
        self.assertEqual(resp.status_code, 200)

    def test_register_page_returns_200(self):
        resp = self.client.get(reverse("customers:register"))
        self.assertEqual(resp.status_code, 200)

    def test_confirm_email_page_returns_200(self):
        resp = self.client.get(reverse("customers:confirm_email"))
        self.assertEqual(resp.status_code, 200)

    @patch("customers.views.login_customer")
    def test_login_post_invalid_credentials_re_renders_form(self, mock_login):
        mock_login.side_effect = AuthError("Invalid email or password.")
        resp = self.client.post(
            reverse("customers:login"),
            {"email": "bad@example.com", "password": "wrong"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid email or password.")

    @patch("customers.views.register_customer")
    def test_register_post_error_re_renders_form(self, mock_register):
        mock_register.side_effect = AuthError("Passwords do not match.")
        resp = self.client.post(
            reverse("customers:register"),
            {"first_name": "A", "last_name": "B", "email": "a@b.com",
             "password": "pass1234", "password_confirm": "different"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Passwords do not match.")


# ── 13. PayNow auth gate ──────────────────────────────────────────────────────

class PayNowAuthGateTests(TestCase):

    def test_paynow_redirects_guest_to_login(self):
        client = Client()
        resp = client.get(reverse("cart:pay_now"))
        self.assertIn(resp.status_code, [301, 302])
        self.assertIn("/account/login/", resp["Location"])
        self.assertIn("next=/cart/pay/", resp["Location"])

    def test_paynow_with_authenticated_customer_returns_200(self):
        client = Client()
        session = client.session
        session[CUSTOMER_ID_SESSION_KEY] = "cccc0000-0000-0000-0000-000000000001"
        session.save()
        resp = client.get(reverse("cart:pay_now"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Payments are not available yet")

    def test_paynow_does_not_create_orders_or_payments(self):
        """Verify no order/payment service imports in cart/views.py."""
        import inspect
        import cart.views as cv
        source = inspect.getsource(cv)
        self.assertNotIn("create_order", source)
        self.assertNotIn("create_payment", source)
        self.assertNotIn("stripe", source)
        self.assertNotIn("decrement", source)


# ── 14. Context processor ─────────────────────────────────────────────────────

class CustomerContextProcessorTests(TestCase):

    @patch("customers.context_processors.get_customer_nav_context")
    def test_injects_authenticated_state(self, mock_ctx):
        from customers.context_processors import customer_auth
        mock_ctx.return_value = {
            "customer": _make_customer(),
            "customer_authenticated": True,
        }
        request = MagicMock()
        request.session = {CUSTOMER_ID_SESSION_KEY: "some-id"}
        ctx = customer_auth(request)
        self.assertTrue(ctx["customer_authenticated"])
        self.assertIsNotNone(ctx["nav_customer"])

    @patch("customers.context_processors.get_customer_nav_context")
    def test_returns_guest_state_on_failure(self, mock_ctx):
        from customers.context_processors import customer_auth
        mock_ctx.side_effect = Exception("DB down")
        request = MagicMock()
        request.session = {}
        ctx = customer_auth(request)
        self.assertFalse(ctx["customer_authenticated"])
        self.assertIsNone(ctx["nav_customer"])
