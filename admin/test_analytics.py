from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from admin.analytics import (
    AnalyticsError,
    _period_metrics,
    _qualifying_orders,
    parse_report_range,
    request_product_opportunities,
)
from admin.pdf_reports import build_analytics_pdf


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
    @patch("admin.analytics._rows")
    def test_paid_orders_are_counted_before_fulfilment_completes(self, mock_rows):
        processing_order = {
            "order_id": "paid-1", "customer_id": "customer-1", "order_status": "PROCESSING",
            "payment_status": "PAID", "total_amount": "1200.00",
            "order_date": "2026-09-10T10:00:00+00:00", "created_at": "2026-09-09T10:00:00+00:00",
        }
        mock_rows.side_effect = [[processing_order], [processing_order]]

        orders = _qualifying_orders(date(2026, 9, 1), date(2026, 9, 30))

        self.assertEqual(orders, [processing_order])
        report = _period_metrics(date(2026, 9, 1), date(2026, 9, 30), orders, [], [], [], [])
        self.assertEqual(report["order_count"], 1)
        self.assertEqual(report["gross_sales"], Decimal("1200.00"))
        self.assertEqual(report["average_order_value"], Decimal("1200.00"))

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


class AnalyticsPdfTests(SimpleTestCase):
    def test_pdf_report_contains_report_heading_and_metrics(self):
        report = _period_metrics(date(2026, 9, 1), date(2026, 9, 1), [], [], [], [], [])
        report["range"] = {"start": date(2026, 9, 1), "end": date(2026, 9, 1)}

        pdf = build_analytics_pdf(report)

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1_000)


class OpportunityRequestTests(SimpleTestCase):
    @patch("admin.analytics.settings.OPENROUTER_API_KEY", "test-key")
    @patch("admin.analytics.httpx.post")
    def test_opportunity_request_disables_reasoning_and_accepts_json(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"suggestions":[{"product_or_category":"Chocolate","rationale":"Popular","supporting_signals":["Sales"],"target_segment":"repeat customers","confidence":"high","cautions":["Check stock"]}]}'}}]
        }
        mock_post.return_value = mock_response

        suggestions = request_product_opportunities({"top_products": []})

        self.assertEqual(suggestions[0]["product_or_category"], "Chocolate")
        self.assertFalse(mock_post.call_args.kwargs["json"]["reasoning"]["enabled"])
        self.assertEqual(mock_post.call_args.kwargs["json"]["max_tokens"], 1800)

    @patch("admin.analytics.settings.OPENROUTER_API_KEY", "test-key")
    @patch("admin.analytics.httpx.post")
    def test_opportunity_request_rejects_empty_ai_content(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": None}}]}
        mock_post.return_value = mock_response

        with self.assertRaises(AnalyticsError):
            request_product_opportunities({"top_products": []})
