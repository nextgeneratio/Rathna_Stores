"""
Admin app tests — staff view access control and error-handling paths.
All Supabase calls are mocked.
Run with:  python manage.py test admin
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile


def _make_category(**kw):
    base = {
        "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Chocolate",
        "slug": "chocolate",
        "description": None,
        "is_active": True,
    }
    base.update(kw)
    return base


def _make_product(**kw):
    from cakes.services import enrich_product
    base = {
        "product_id": "bbbbbbbb-0000-0000-0000-000000000001",
        "category_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "Choco Fudge",
        "description": "Rich",
        "weight_value": None,
        "weight_unit": None,
        "serving_count": None,
        "serving_unit": None,
        "stock_quantity": 5,
        "price": "3800.00",
        "discount_type": "NONE",
        "discount_value": "0",
        "is_active": True,
        "categories": _make_category(),
    }
    base.update(kw)
    return enrich_product(base)


class StaffViewErrorHandlingTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.client.login(username="staff", password="pw")

    @patch("admin.views.get_all_products_staff", side_effect=Exception("DB down"))
    @patch("admin.views.get_all_categories", return_value=[])
    def test_dashboard_handles_service_failure(self, mock_cats, mock_products):
        """Dashboard must not 500 when Supabase is unavailable."""
        response = self.client.get(reverse("store_admin:dashboard"))
        self.assertEqual(response.status_code, 200)

    @patch("admin.views.get_all_products_staff", side_effect=Exception("DB down"))
    def test_product_list_handles_service_failure(self, mock_products):
        response = self.client.get(reverse("store_admin:product_list"))
        self.assertEqual(response.status_code, 200)

    @patch("admin.views.get_all_categories", side_effect=Exception("DB down"))
    def test_category_list_handles_service_failure(self, mock_cats):
        response = self.client.get(reverse("store_admin:category_list"))
        self.assertEqual(response.status_code, 200)

    @patch("admin.views.get_product_by_id", return_value=None)
    def test_product_edit_404_for_missing_product(self, mock_get):
        pid = "bbbbbbbb-0000-0000-0000-000000000099"
        response = self.client.get(reverse("store_admin:product_edit", args=[pid]))
        self.assertEqual(response.status_code, 404)

    @patch("admin.views.get_category_by_id", return_value=None)
    def test_category_edit_404_for_missing_category(self, mock_get):
        cid = "aaaaaaaa-0000-0000-0000-000000000099"
        response = self.client.get(reverse("store_admin:category_edit", args=[cid]))
        self.assertEqual(response.status_code, 404)

    @patch("admin.views.get_product_by_id", return_value=None)
    def test_product_images_404_for_missing_product(self, mock_get):
        pid = "bbbbbbbb-0000-0000-0000-000000000099"
        response = self.client.get(reverse("store_admin:product_images", args=[pid]))
        self.assertEqual(response.status_code, 404)


class ImageUploadTests(TestCase):

    def setUp(self):
        self.client = Client()
        User.objects.create_user("image_staff", password="pw", is_staff=True)
        self.client.login(username="image_staff", password="pw")
        self.product_id = "33333333-3333-4333-8333-333333333333"
        self.product = {
            "product_id": self.product_id,
            "name": "Dark Mocha Cake",
            "categories": {"slug": "chocolate"},
        }

    @patch("admin.views.set_primary_image")
    @patch("admin.views.add_product_image")
    @patch("admin.views.get_public_url", return_value="https://example.test/new.jpg")
    @patch("admin.views.upload_image_to_storage", return_value="Off_Shelf/chocolate/new.jpg")
    @patch("admin.views.get_images_for_product")
    @patch("admin.views.get_product_by_id")
    def test_primary_upload_is_inserted_before_promotion(
        self,
        mock_get_product,
        mock_get_images,
        mock_upload,
        mock_public_url,
        mock_add,
        mock_set_primary,
    ):
        mock_get_product.return_value = self.product
        mock_get_images.return_value = [
            {"image_id": "old-image", "display_order": 0, "is_primary": True}
        ]
        mock_add.return_value = {"image_id": "new-image"}

        response = self.client.post(
            reverse("store_admin:image_upload", args=[self.product_id]),
            {
                "image_file": SimpleUploadedFile(
                    "new.jpg", b"image data", content_type="image/jpeg"
                ),
                "make_primary": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        inserted_data = mock_add.call_args.args[0]
        self.assertFalse(inserted_data["is_primary"])
        mock_set_primary.assert_called_once_with(self.product_id, "new-image")


class CategoryCreateTests(TestCase):

    def setUp(self):
        self.client = Client()
        User.objects.create_user("staff2", password="pw", is_staff=True)
        self.client.login(username="staff2", password="pw")

    @patch("admin.views.create_category")
    def test_category_create_valid(self, mock_create):
        mock_create.return_value = _make_category(name="Vanilla", slug="vanilla")
        response = self.client.post(
            reverse("store_admin:category_create"),
            {"name": "Vanilla", "slug": "vanilla", "is_active": "1"},
        )
        self.assertEqual(response.status_code, 302)
        mock_create.assert_called_once()

    def test_category_create_invalid_slug(self):
        response = self.client.post(
            reverse("store_admin:category_create"),
            {"name": "Vanilla", "slug": "Bad Slug!", "is_active": "1"},
        )
        self.assertEqual(response.status_code, 200)

    @patch("admin.views.create_category", side_effect=Exception("duplicate key"))
    def test_category_create_db_error_shown_to_user(self, mock_create):
        response = self.client.post(
            reverse("store_admin:category_create"),
            {"name": "Vanilla", "is_active": "1"},
        )
        self.assertEqual(response.status_code, 200)
