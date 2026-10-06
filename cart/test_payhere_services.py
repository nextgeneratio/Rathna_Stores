from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from .payhere_services import (
    _notification_hash,
    _request_hash,
    create_payhere_checkout,
    handle_notification,
)
from .stripe_services import CheckoutError


class PayHereCheckoutTests(SimpleTestCase):
    @override_settings(PAYHERE_MERCHANT_ID="1210000", PAYHERE_MERCHANT_SECRET="sandbox-secret")
    @patch("cart.payhere_services.get_supabase_client")
    @patch("cart.payhere_services._create_pending_order")
    def test_checkout_builds_a_server_signed_sandbox_form(self, create_order, get_client):
        create_order.return_value = (
            {"order_id": "order-1", "order_number": "RS-1"},
            {"total": "1250.00"},
            {},
        )
        checkout = create_payhere_checkout(
            {},
            customer={"first_name": "Test", "last_name": "Customer", "email": "test@example.com", "phone_number": "0770000000"},
            billing_address={"phone_number": "0770000000", "address_line_1": "1 Main Road", "city": "Colombo"},
            notify_url="https://shop.example/payments/payhere/notify/",
            return_url="https://shop.example/cart/pay/success/",
            cancel_url="https://shop.example/cart/pay/cancel/",
        )

        self.assertEqual(checkout["action"], "https://sandbox.payhere.lk/pay/checkout")
        self.assertEqual(checkout["amount"], "1250.00")
        self.assertEqual(checkout["hash"], _request_hash("1210000", "order-1", "1250.00", "LKR", "sandbox-secret"))
        self.assertEqual(checkout["merchant_id"], "1210000")

    def test_checkout_requires_credentials(self):
        with self.assertRaises(CheckoutError):
            create_payhere_checkout(
                {}, customer={}, billing_address={}, notify_url="x", return_url="x", cancel_url="x"
            )

    @override_settings(PAYHERE_MERCHANT_ID="1210000", PAYHERE_MERCHANT_SECRET="sandbox-secret")
    def test_checkout_rejects_localhost_notification_url(self):
        with self.assertRaises(CheckoutError):
            create_payhere_checkout(
                {},
                customer={},
                billing_address={},
                notify_url="https://127.0.0.1:8000/payments/payhere/notify/",
                return_url="https://127.0.0.1:8000/cart/pay/success/",
                cancel_url="https://127.0.0.1:8000/cart/pay/cancel/",
            )

    @override_settings(PAYHERE_MERCHANT_ID="1210000", PAYHERE_MERCHANT_SECRET="sandbox-secret")
    @patch("cart.payhere_services._create_pending_order")
    def test_checkout_rejects_invalid_customer_email_before_order_creation(self, create_order):
        with self.assertRaises(CheckoutError):
            create_payhere_checkout(
                {},
                customer={"first_name": "Test", "last_name": "Customer", "email": "not-an-email", "phone_number": "0770000000"},
                billing_address={"address_line_1": "1 Main Road", "city": "Colombo"},
                notify_url="https://shop.example/payments/payhere/notify/",
                return_url="https://shop.example/cart/pay/success/",
                cancel_url="https://shop.example/cart/pay/cancel/",
            )
        create_order.assert_not_called()


class PayHereNotificationTests(SimpleTestCase):
    @override_settings(PAYHERE_MERCHANT_ID="1210000", PAYHERE_MERCHANT_SECRET="sandbox-secret")
    @patch("cart.payhere_services.get_supabase_client")
    def test_verified_success_calls_atomic_finalizer(self, get_client):
        query = MagicMock()
        query.select.return_value = query
        query.eq.return_value = query
        query.limit.return_value = query
        query.execute.return_value.data = [{"order_id": "order-1", "amount": "1250.00", "payment_status": "PENDING"}]
        client = MagicMock()
        client.table.return_value = query
        get_client.return_value = client
        payload = {
            "merchant_id": "1210000",
            "order_id": "order-1",
            "payment_id": "3200000001",
            "payhere_amount": "1250.00",
            "payhere_currency": "LKR",
            "status_code": "2",
        }
        payload["md5sig"] = _notification_hash(
            "1210000", "order-1", "1250.00", "LKR", "2", "sandbox-secret"
        )

        handle_notification(payload)

        client.rpc.assert_called_once_with("finalize_payhere_payment", {
            "p_event_id": "3200000001:2",
            "p_order_id": "order-1",
            "p_payment_id": "3200000001",
            "p_amount": "1250.00",
        })

    @override_settings(PAYHERE_MERCHANT_ID="1210000", PAYHERE_MERCHANT_SECRET="sandbox-secret")
    def test_invalid_notification_signature_is_rejected(self):
        with self.assertRaises(CheckoutError):
            handle_notification({
                "merchant_id": "1210000", "order_id": "order-1", "payment_id": "payment-1",
                "payhere_amount": "1250.00", "payhere_currency": "LKR", "status_code": "2",
                "md5sig": "INVALID",
            })
