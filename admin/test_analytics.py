from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from admin.analytics import AnalyticsError, _period_metrics, parse_report_range


class AnalyticsRangeTests(SimpleTestCase):
    def test_default_range_is_thirty_inclusive_days(self):
        start, end = parse_report_range("2026-09-01", "2026-09-30")
        self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 30)))

    def test_range_is_bounded(self):
        with self.assertRaises(AnalyticsError):
            parse_report_range("2026-01-01", "2026-04-01")

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(AnalyticsError):
            parse_report_range("2026-09-30", "2026-09-01")


class AnalyticsAggregationTests(SimpleTestCase):
    def test_only_passed_orders_are_aggregated_and_products_have_tie_breaker(self):
        orders = [
            {"order_id": "1", "customer_id": "a", "order_status": "COMPLETED", "payment_status": "PAID", "total_amount": "100", "created_at": "2026-09-10T10:00:00+00:00"},
            {"order_id": "2", "customer_id": "b", "order_status": "PENDING", "payment_status": "PAID", "total_amount": "900", "created_at": "2026-09-10T10:00:00+00:00"},
        ]
        items = [
            {"order_id": "1", "product_id": "p2", "product_name_snapshot": "B Cake", "quantity": 2, "item_total": "80"},
            {"order_id": "1", "product_id": "p1", "product_name_snapshot": "A Cake", "quantity": 2, "item_total": "80"},
        ]
        report = _period_metrics(date(2026, 9, 1), date(2026, 9, 10), orders[:1], items, [], [{"customer_id": "a", "is_active": True}], [{"product_id": "p1", "name": "A Cake", "stock_quantity": 3}, {"product_id": "p2", "name": "B Cake", "stock_quantity": 4}])
        self.assertEqual(report["order_count"], 1)
        self.assertEqual(report["gross_sales"], Decimal("100"))
        self.assertEqual([item["product_name"] for item in report["best_sellers"]], ["A Cake", "B Cake"])
