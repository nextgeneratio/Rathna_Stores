"""
Cakes app tests.

All Supabase client calls are mocked — no live Supabase access is required.
Run with:  python manage.py test cakes
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.contrib.auth.models import User


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _make_category(**overrides):
    base = {
        "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Chocolate",
        "slug": "chocolate",
        "description": "Rich chocolate cakes",
        "is_active": True,
    }
    base.update(overrides)
    return base


def _make_product(**overrides):
    base = {
        "product_id": "bbbbbbbb-0000-0000-0000-000000000001",
        "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Choco Fudge",
        "description": "Rich and moist",
        "weight_value": "1.00",
        "weight_unit": "kg",
        "serving_count": 8,
        "serving_unit": "persons",
        "stock_quantity": 10,
        "price": "3800.00",
        "discount_type": "NONE",
        "discount_value": "0",
        "is_active": True,
        "categories": _make_category(),
    }
    base.update(overrides)
    return base


def _make_image(**overrides):
    base = {
        "image_id": "cccccccc-0000-0000-0000-000000000001",
        "product_id": "bbbbbbbb-0000-0000-0000-000000000001",
        "image_url": "https://example.com/cake.jpg",
        "alt_text": "Choco Fudge cake",
        "display_order": 0,
        "is_primary": True,
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────────────
# 1. Pricing helpers
# ────────────────────────────────────────────────────────────────────────────

class PricingTests(TestCase):

    def test_no_discount(self):
        from cakes.services import calculate_effective_price
        result = calculate_effective_price(Decimal("3800"), "NONE", Decimal("0"))
        self.assertEqual(result, Decimal("3800.00"))

    def test_percent_discount(self):
        from cakes.services import calculate_effective_price
        result = calculate_effective_price(Decimal("3800"), "PERCENT", Decimal("10"))
        self.assertEqual(result, Decimal("3420.00"))

    def test_amount_discount(self):
        from cakes.services import calculate_effective_price
        result = calculate_effective_price(Decimal("3800"), "AMOUNT", Decimal("500"))
        self.assertEqual(result, Decimal("3300.00"))

    def test_discount_cannot_produce_negative_price(self):
        from cakes.services import calculate_effective_price
        result = calculate_effective_price(Decimal("200"), "AMOUNT", Decimal("500"))
        self.assertEqual(result, Decimal("0.00"))

    def test_100_percent_discount(self):
        from cakes.services import calculate_effective_price
        result = calculate_effective_price(Decimal("3800"), "PERCENT", Decimal("100"))
        self.assertEqual(result, Decimal("0.00"))

    def test_format_lkr_whole(self):
        from cakes.services import format_lkr
        self.assertEqual(format_lkr(Decimal("3800")), "LKR 3,800")

    def test_format_lkr_decimal(self):
        from cakes.services import format_lkr
        self.assertEqual(format_lkr(Decimal("3800.50")), "LKR 3,800.50")

    def test_format_lkr_thousands(self):
        from cakes.services import format_lkr
        self.assertEqual(format_lkr(Decimal("12500")), "LKR 12,500")

    def test_enrich_product_no_discount(self):
        from cakes.services import enrich_product
        p = _make_product()
        enriched = enrich_product(p)
        self.assertFalse(enriched["has_discount"])
        self.assertEqual(enriched["effective_price"], Decimal("3800.00"))
        self.assertEqual(enriched["effective_price_display"], "LKR 3,800")
        self.assertTrue(enriched["in_stock"])

    def test_enrich_product_with_percent_discount(self):
        from cakes.services import enrich_product
        p = _make_product(discount_type="PERCENT", discount_value="10")
        enriched = enrich_product(p)
        self.assertTrue(enriched["has_discount"])
        self.assertEqual(enriched["effective_price"], Decimal("3420.00"))
        self.assertEqual(enriched["discount_display"], "10% off")

    def test_enrich_product_out_of_stock(self):
        from cakes.services import enrich_product
        p = _make_product(stock_quantity=0)
        enriched = enrich_product(p)
        self.assertFalse(enriched["in_stock"])


# ────────────────────────────────────────────────────────────────────────────
# 2. Image helpers
# ────────────────────────────────────────────────────────────────────────────

class ImageHelperTests(TestCase):

    def test_pick_primary_image_returns_primary(self):
        from cakes.services import pick_primary_image
        images = [
            _make_image(display_order=0, is_primary=False),
            _make_image(display_order=1, is_primary=True),
        ]
        result = pick_primary_image(images)
        self.assertTrue(result["is_primary"])

    def test_pick_primary_image_falls_back_to_first(self):
        from cakes.services import pick_primary_image
        images = [
            _make_image(display_order=0, is_primary=False),
            _make_image(display_order=1, is_primary=False),
        ]
        result = pick_primary_image(images)
        self.assertEqual(result["display_order"], 0)

    def test_pick_primary_image_empty(self):
        from cakes.services import pick_primary_image
        self.assertIsNone(pick_primary_image([]))


# ────────────────────────────────────────────────────────────────────────────
# 3. Catalogue queries (mocked Supabase)
# ────────────────────────────────────────────────────────────────────────────

class CatalogueServiceTests(TestCase):

    def _mock_client(self, product_rows=None, category_rows=None, image_rows=None):
        """Build a MagicMock supabase client that returns given rows."""
        mock_client = MagicMock()

        # Fluent chained query builder — always returns the same mock
        query_mock = MagicMock()
        query_mock.select.return_value = query_mock
        query_mock.eq.return_value = query_mock
        query_mock.or_.return_value = query_mock
        query_mock.gt.return_value = query_mock
        query_mock.order.return_value = query_mock
        query_mock.limit.return_value = query_mock
        query_mock.in_.return_value = query_mock
        query_mock.execute.return_value = MagicMock(data=product_rows or [])

        mock_client.table.return_value = query_mock
        return mock_client

    @patch("cakes.services.get_supabase_client")
    def test_get_active_categories_returns_data(self, mock_get_client):
        from cakes.services import get_active_categories
        mock_client = self._mock_client(product_rows=[_make_category()])
        mock_get_client.return_value = mock_client

        result = get_active_categories()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Chocolate")

    @patch("cakes.services.get_supabase_client")
    def test_get_active_categories_handles_api_error(self, mock_get_client):
        from cakes.services import get_active_categories
        from postgrest.exceptions import APIError
        mock_client = MagicMock()
        mock_client.table.side_effect = APIError({"message": "DB error"})
        mock_get_client.return_value = mock_client

        result = get_active_categories()
        self.assertEqual(result, [])

    @patch("cakes.services._fetch_images_for_products")
    @patch("cakes.services.get_supabase_client")
    def test_get_catalogue_products_enriches(self, mock_get_client, mock_images):
        from cakes.services import get_catalogue_products
        mock_client = self._mock_client(product_rows=[_make_product()])
        mock_get_client.return_value = mock_client
        mock_images.return_value = {"bbbbbbbb-0000-0000-0000-000000000001": [_make_image()]}

        result = get_catalogue_products()
        self.assertEqual(len(result), 1)
        self.assertIn("effective_price", result[0])
        self.assertIn("primary_image", result[0])

    @patch("cakes.services._fetch_images_for_products")
    @patch("cakes.services.get_supabase_client")
    def test_get_catalogue_products_filters_by_price_preset(self, mock_get_client, mock_images):
        from cakes.services import get_catalogue_products
        mock_client = self._mock_client(product_rows=[
            _make_product(product_id="p1", price="1000.00"),
            _make_product(product_id="p2", price="4000.00"),
        ])
        mock_get_client.return_value = mock_client
        mock_images.return_value = {}

        result = get_catalogue_products(price_preset="under-1500")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["effective_price"], Decimal("1000.00"))

    @patch("cakes.services._fetch_images_for_product")
    @patch("cakes.services.get_supabase_client")
    def test_get_product_by_id_returns_product(self, mock_get_client, mock_images):
        from cakes.services import get_product_by_id
        mock_client = self._mock_client(product_rows=[_make_product()])
        mock_get_client.return_value = mock_client
        mock_images.return_value = [_make_image()]

        result = get_product_by_id("bbbbbbbb-0000-0000-0000-000000000001")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Choco Fudge")

    @patch("cakes.services.get_supabase_client")
    def test_get_product_by_id_invalid_uuid_returns_none(self, mock_get_client):
        from cakes.services import get_product_by_id
        result = get_product_by_id("not-a-uuid")
        self.assertIsNone(result)
        mock_get_client.assert_not_called()

    @patch("cakes.services._fetch_images_for_product")
    @patch("cakes.services.get_supabase_client")
    def test_get_product_by_id_not_found_returns_none(self, mock_get_client, mock_images):
        from cakes.services import get_product_by_id
        mock_client = self._mock_client(product_rows=[])
        mock_get_client.return_value = mock_client
        mock_images.return_value = []

        result = get_product_by_id("bbbbbbbb-0000-0000-0000-000000000001")
        self.assertIsNone(result)


# ────────────────────────────────────────────────────────────────────────────
# 4. Public views
# ────────────────────────────────────────────────────────────────────────────

class CatalogueViewTests(TestCase):

    def setUp(self):
        self.client = Client()

    @patch("cakes.views.get_active_categories", return_value=[_make_category()])
    @patch("cakes.views.get_catalogue_products", return_value=[enrich := None])
    def test_catalogue_returns_200(self, mock_products, mock_categories):
        # Patch the return value properly
        from cakes.services import enrich_product
        enriched = enrich_product(_make_product())
        enriched["primary_image"] = _make_image()
        mock_products.return_value = [enriched]

        response = self.client.get(reverse("cakes:catalogue"))
        self.assertEqual(response.status_code, 200)

    @patch("cakes.views.get_active_categories", return_value=[])
    @patch("cakes.views.get_catalogue_products", return_value=[])
    def test_catalogue_empty_state(self, mock_products, mock_categories):
        response = self.client.get(reverse("cakes:catalogue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No cakes found")

    @patch("cakes.views.get_approved_reviews_for_product", return_value={"reviews": [], "average_rating": None, "count": 0})
    @patch("cakes.views.get_product_by_id")
    def test_product_detail_returns_200(self, mock_product, mock_reviews):
        from cakes.services import enrich_product
        enriched = enrich_product(_make_product())
        enriched["images"] = [_make_image()]
        enriched["primary_image"] = _make_image()
        mock_product.return_value = enriched

        pid = "bbbbbbbb-0000-0000-0000-000000000001"
        response = self.client.get(reverse("cakes:product_detail", args=[pid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choco Fudge")

    @patch("cakes.views.get_product_by_id", return_value=None)
    def test_product_detail_404_for_missing(self, mock_product):
        pid = "bbbbbbbb-0000-0000-0000-000000000099"
        response = self.client.get(reverse("cakes:product_detail", args=[pid]))
        self.assertEqual(response.status_code, 404)

    def test_product_detail_invalid_uuid_returns_404(self):
        response = self.client.get("/product/not-valid-uuid/")
        self.assertEqual(response.status_code, 404)

    @patch("cakes.views.get_active_categories", return_value=[_make_category()])
    @patch("cakes.views.get_catalogue_products", return_value=[])
    def test_catalogue_search_param_passed_through(self, mock_products, mock_categories):
        response = self.client.get(reverse("cakes:catalogue") + "?q=chocolate")
        self.assertEqual(response.status_code, 200)
        mock_products.assert_called_once()
        call_kwargs = mock_products.call_args.kwargs
        self.assertEqual(call_kwargs.get("search"), "chocolate")

    @patch("cakes.views.get_active_categories", return_value=[_make_category()])
    @patch("cakes.views.get_catalogue_products", return_value=[])
    def test_catalogue_in_stock_filter(self, mock_products, mock_categories):
        response = self.client.get(reverse("cakes:catalogue") + "?in_stock=1")
        self.assertEqual(response.status_code, 200)
        call_kwargs = mock_products.call_args.kwargs
        self.assertTrue(call_kwargs.get("in_stock_only"))


# ────────────────────────────────────────────────────────────────────────────
# 5. Staff access control
# ────────────────────────────────────────────────────────────────────────────

class StaffAccessTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            username="staffmember", password="testpassword", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="customer", password="testpassword", is_staff=False
        )

    def test_dashboard_redirects_anonymous(self):
        response = self.client.get(reverse("store_admin:dashboard"))
        self.assertIn(response.status_code, [301, 302])

    def test_dashboard_forbids_non_staff(self):
        self.client.login(username="customer", password="testpassword")
        response = self.client.get(reverse("store_admin:dashboard"))
        self.assertEqual(response.status_code, 403)

    @patch("admin.views.get_all_products_staff", return_value=[])
    @patch("admin.views.get_all_categories", return_value=[])
    def test_dashboard_allows_staff(self, mock_cats, mock_products):
        self.client.login(username="staffmember", password="testpassword")
        response = self.client.get(reverse("store_admin:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_product_list_redirects_anonymous(self):
        response = self.client.get(reverse("store_admin:product_list"))
        self.assertIn(response.status_code, [301, 302])

    def test_product_list_forbids_non_staff(self):
        self.client.login(username="customer", password="testpassword")
        response = self.client.get(reverse("store_admin:product_list"))
        self.assertEqual(response.status_code, 403)

    @patch("admin.views.get_all_products_staff", return_value=[])
    def test_product_list_allows_staff(self, mock_products):
        self.client.login(username="staffmember", password="testpassword")
        response = self.client.get(reverse("store_admin:product_list"))
        self.assertEqual(response.status_code, 200)

    def test_category_list_forbids_non_staff(self):
        self.client.login(username="customer", password="testpassword")
        response = self.client.get(reverse("store_admin:category_list"))
        self.assertEqual(response.status_code, 403)


# ────────────────────────────────────────────────────────────────────────────
# 6. Staff form validation (unit-level, no HTTP)
# ────────────────────────────────────────────────────────────────────────────

class ProductFormValidationTests(TestCase):

    def _post(self, overrides=None):
        base = {
            "name": "Test Cake",
            "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "price": "2500",
            "stock_quantity": "5",
            "discount_type": "NONE",
            "discount_value": "0",
            "is_active": "1",
        }
        if overrides:
            base.update(overrides)
        return base

    def test_valid_form_no_errors(self):
        from admin.views import _validate_product_form
        data, errors = _validate_product_form(self._post())
        self.assertEqual(errors, [])
        self.assertEqual(data["name"], "Test Cake")

    def test_missing_name_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"name": ""}))
        self.assertTrue(any("name" in e.lower() for e in errors))

    def test_negative_price_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"price": "-100"}))
        self.assertTrue(any("price" in e.lower() or "negative" in e.lower() for e in errors))

    def test_negative_stock_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"stock_quantity": "-1"}))
        self.assertTrue(any("stock" in e.lower() or "negative" in e.lower() for e in errors))

    def test_none_discount_with_nonzero_value_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"discount_type": "NONE", "discount_value": "100"}))
        self.assertTrue(len(errors) > 0)

    def test_percent_discount_out_of_range_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"discount_type": "PERCENT", "discount_value": "150"}))
        self.assertTrue(len(errors) > 0)

    def test_percent_discount_valid(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"discount_type": "PERCENT", "discount_value": "20"}))
        self.assertEqual(errors, [])

    def test_amount_discount_zero_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"discount_type": "AMOUNT", "discount_value": "0"}))
        self.assertTrue(len(errors) > 0)

    def test_invalid_uuid_category_raises_error(self):
        from admin.views import _validate_product_form
        _, errors = _validate_product_form(self._post({"category_id": "not-a-uuid"}))
        self.assertTrue(any("category" in e.lower() for e in errors))


class CategoryFormValidationTests(TestCase):

    def test_valid_form(self):
        from admin.views import _validate_category_form
        data, errors = _validate_category_form({"name": "Chocolate", "is_active": "1"})
        self.assertEqual(errors, [])
        self.assertEqual(data["name"], "Chocolate")

    def test_missing_name_raises_error(self):
        from admin.views import _validate_category_form
        _, errors = _validate_category_form({"name": ""})
        self.assertTrue(len(errors) > 0)

    def test_invalid_slug_raises_error(self):
        from admin.views import _validate_category_form
        _, errors = _validate_category_form({"name": "Test", "slug": "Bad Slug!"})
        self.assertTrue(len(errors) > 0)

    def test_slug_auto_generated(self):
        from admin.views import _validate_category_form
        data, errors = _validate_category_form({"name": "Dark Chocolate", "is_active": "1"})
        self.assertEqual(errors, [])
        self.assertEqual(data["slug"], "dark-chocolate")

    def test_valid_slug_accepted(self):
        from admin.views import _validate_category_form
        data, errors = _validate_category_form({"name": "Test", "slug": "my-slug-123", "is_active": "1"})
        self.assertEqual(errors, [])
        self.assertEqual(data["slug"], "my-slug-123")


# ────────────────────────────────────────────────────────────────────────────
# 7. Staff POST views — product toggle and create (mocked)
# ────────────────────────────────────────────────────────────────────────────

class StaffProductPostTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            username="staffmember2", password="testpass", is_staff=True
        )
        self.client.login(username="staffmember2", password="testpass")

    @patch("admin.views.get_product_by_id")
    @patch("admin.views.update_product")
    def test_product_toggle_active_to_inactive(self, mock_update, mock_get):
        from cakes.services import enrich_product
        mock_get.return_value = enrich_product(_make_product(is_active=True))
        mock_update.return_value = enrich_product(_make_product(is_active=False))

        pid = "bbbbbbbb-0000-0000-0000-000000000001"
        response = self.client.post(reverse("store_admin:product_toggle", args=[pid]))
        self.assertEqual(response.status_code, 302)
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        self.assertFalse(call_args[0][1]["is_active"])

    @patch("admin.views.get_all_categories", return_value=[_make_category()])
    @patch("admin.views.create_product")
    def test_product_create_valid_redirects(self, mock_create, mock_cats):
        from cakes.services import enrich_product
        mock_create.return_value = enrich_product(_make_product())

        response = self.client.post(
            reverse("store_admin:product_create"),
            {
                "name": "New Cake",
                "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "price": "2500",
                "stock_quantity": "3",
                "discount_type": "NONE",
                "discount_value": "0",
                "is_active": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        mock_create.assert_called_once()

    @patch("admin.views.get_all_categories", return_value=[_make_category()])
    def test_product_create_invalid_returns_form(self, mock_cats):
        response = self.client.post(
            reverse("store_admin:product_create"),
            {
                "name": "",  # missing required name
                "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
                "price": "2500",
                "stock_quantity": "3",
                "discount_type": "NONE",
                "discount_value": "0",
            },
        )
        self.assertEqual(response.status_code, 200)  # stays on form
