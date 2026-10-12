from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .payment_services import (
    confirm_offline_payment,
    create_offline_payment_order,
    get_pending_offline_payments,
)


class OfflinePaymentServiceTests(SimpleTestCase):
    @patch("cart.payment_services._finalize_paid_order")
    @patch("cart.payment_services.get_supabase_client")
    @patch("cart.payment_services._create_pending_order")
    def test_payment_request_completes_paid_order(self, create_pending, get_client, finalize):
        create_pending.return_value = (
            {"order_id": "order-1", "order_number": "RS-1", "customer_id": "customer-1"},
            {"total": "2500.00"},
            {},
        )
        client = MagicMock()
        get_client.return_value = client

        result = create_offline_payment_order(
            {"rathna_customer_id": "customer-1"},
            "PICKUP",
            None,
            "BANK_TRANSFER",
            "BANK-1234",
        )

        self.assertEqual(result["order_id"], "order-1")
        payment_insert = client.table.return_value.insert.call_args_list[0].args[0]
        self.assertEqual(payment_insert["payment_status"], "SUCCEEDED")
        self.assertEqual(payment_insert["payment_reference"], "BANK-1234")
        finalize.assert_called_once_with(client, "order-1", "customer-1")

    @patch("cart.payment_services._finalize_paid_order")
    @patch("cart.payment_services.get_supabase_client")
    def test_confirmation_updates_order_and_payment(self, get_client, finalize):
        client = MagicMock()
        get_client.return_value = client
        order_query = MagicMock()
        payment_query = MagicMock()
        client.table.side_effect = lambda table: order_query if table == "orders" else payment_query
        order_query.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            "order_id": "order-1", "customer_id": "customer-1", "payment_status": "PENDING", "order_status": "PENDING",
        }]
        payment_query.select.return_value = payment_query
        payment_query.eq.return_value = payment_query
        payment_query.in_.return_value = payment_query
        payment_query.limit.return_value = payment_query
        payment_query.execute.return_value.data = [{
            "payment_id": "payment-1", "payment_status": "PENDING",
        }]

        result = confirm_offline_payment("order-1")

        self.assertEqual(result["payment_status"], "PAID")
        finalize.assert_called_once_with(client, "order-1", "customer-1")
        self.assertGreaterEqual(payment_query.update.call_count, 1)
        payment_query.in_.assert_called_once_with("payment_method", ["BANK_TRANSFER"])
        order_query.update.assert_called_once()

    @patch("cart.payment_services.get_supabase_client")
    def test_pending_staff_review_is_limited_to_bank_transfers(self, get_client):
        query = MagicMock()
        query.select.return_value = query
        query.eq.return_value = query
        query.in_.return_value = query
        query.order.return_value = query
        query.execute.return_value.data = []
        client = MagicMock()
        client.table.return_value = query
        get_client.return_value = client

        self.assertEqual(get_pending_offline_payments(), [])

        query.in_.assert_called_once_with("payment_method", ["BANK_TRANSFER"])
