from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from .delivery_services import DeliveryError, delivery_quote


class DeliveryQuoteTests(SimpleTestCase):
    @override_settings(
        STORE_LATITUDE="6.9271",
        STORE_LONGITUDE="79.8612",
        DELIVERY_BASE_FEE_LKR="150.00",
        DELIVERY_RATE_PER_KM_LKR="80.00",
        DELIVERY_ESTIMATE_HOURS=24,
    )
    def test_quote_contains_distance_fee_and_24_hour_estimate(self):
        quote = delivery_quote({"district": "Colombo", "latitude": "6.9271", "longitude": "79.8612"})
        self.assertEqual(quote["distance_km"], Decimal("0.00"))
        self.assertEqual(quote["fee"], Decimal("150.00"))
        self.assertIsNotNone(quote["estimated_at"])

    def test_non_colombo_destination_is_rejected(self):
        with self.assertRaises(DeliveryError):
            delivery_quote({"district": "Kandy", "latitude": "7.2906", "longitude": "80.6337"})

    def test_missing_coordinates_are_rejected(self):
        with self.assertRaises(DeliveryError):
            delivery_quote({"district": "Colombo"})
