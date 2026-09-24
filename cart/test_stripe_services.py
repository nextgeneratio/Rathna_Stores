from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from .stripe_services import CheckoutError, create_checkout_session, handle_webhook


class StripeConfigurationTests(SimpleTestCase):
    @override_settings(STRIPE_TEST_MODE=True, STRIPE_SECRET_KEY="sk_live_never")
    def test_live_mode_key_fails_closed(self):
        with self.assertRaises(CheckoutError):
            create_checkout_session({"customer_id": "customer"}, "http://testserver")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("cart.stripe_services.stripe.Webhook.construct_event", side_effect=Exception("bad signature"))
    def test_invalid_webhook_signature_is_rejected(self, mock_construct):
        with self.assertRaises(CheckoutError):
            handle_webhook(b"{}", "invalid")
        mock_construct.assert_called_once()


class StripeCheckoutServiceTests(SimpleTestCase):
    @override_settings(STRIPE_TEST_MODE=True, STRIPE_SECRET_KEY="sk_test_example")
    @patch("cart.stripe_services.stripe.checkout.Session.create")
    @patch("cart.stripe_services.get_supabase_client")
    @patch("cart.stripe_services.get_cart")
    def test_session_uses_effective_server_cart_values(self, mock_cart, mock_client, mock_create):
        mock_cart.return_value = {
            "items": [{
                "product_id": "product-1",
                "effective_qty": 2,
                "quantity": 4,
                "unit_price": "1250.00",
                "product": {"name": "Chocolate Cake"},
                "is_available": True,
            }],
            "subtotal": "2500.00",
            "has_purchasable_items": True,
        }
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value.data = [{
            "order_id": "order-1",
            "order_number": "RS-TEST",
        }]
        mock_client.return_value = client
        mock_create.return_value = {"id": "cs_test_1", "url": "https://checkout.test/session"}

        from customers.services import CUSTOMER_ID_SESSION_KEY
        url = create_checkout_session({CUSTOMER_ID_SESSION_KEY: "customer-1"}, "http://testserver")

        self.assertEqual(url, "https://checkout.test/session")
        line_item = mock_create.call_args.kwargs["line_items"][0]
        self.assertEqual(line_item["quantity"], 2)
        self.assertEqual(line_item["price_data"]["unit_amount"], 125000)
        self.assertEqual(mock_create.call_args.kwargs["metadata"], {"order_id": "order-1"})

    @override_settings(STRIPE_TEST_MODE=True, STRIPE_SECRET_KEY="sk_test_example", DELIVERY_FEE_LKR="500.00")
    @patch("cart.stripe_services.stripe.checkout.Session.create", return_value={"id": "cs_test_2", "url": "https://checkout.test/session"})
    @patch("cart.stripe_services.get_supabase_client")
    @patch("cart.stripe_services.get_cart")
    def test_delivery_requires_colombo_and_snapshots_address(self, mock_cart, mock_client, mock_create):
        mock_cart.return_value = {
            "items": [{"product_id": "product-1", "quantity": 1, "effective_qty": 1, "unit_price": "1250.00", "product": {"name": "Chocolate Cake"}, "is_available": True}],
            "subtotal": "1250.00", "has_purchasable_items": True,
        }
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value.data = [{"order_id": "order-2", "order_number": "RS-TEST-2"}]
        mock_client.return_value = client
        from customers.services import CUSTOMER_ID_SESSION_KEY

        address = {"recipient_name": "Test Customer", "phone_number": "0770000000", "address_line_1": "1 Main Road", "city": "Colombo", "district": "Colombo", "postal_code": "00100"}
        create_checkout_session({CUSTOMER_ID_SESSION_KEY: "customer-1"}, "http://testserver", "DELIVERY", address)

        order_payload = client.table.return_value.insert.call_args_list[0].args[0]
        self.assertEqual(order_payload["delivery_type"], "DELIVERY")
        self.assertEqual(order_payload["delivery_district"], "Colombo")
        self.assertEqual(order_payload["delivery_fee"], "500.00")
        self.assertEqual(order_payload["total_amount"], "1750.00")

        with self.assertRaises(CheckoutError):
            create_checkout_session({CUSTOMER_ID_SESSION_KEY: "customer-1"}, "http://testserver", "DELIVERY", {**address, "district": "Kandy"})


class StripeWebhookServiceTests(SimpleTestCase):
    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("cart.stripe_services.stripe.Webhook.construct_event")
    @patch("cart.stripe_services.get_supabase_client")
    def test_paid_event_calls_transactional_finalizer(self, mock_client, mock_construct):
        mock_construct.return_value = {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": "cs_test_1",
                "payment_status": "paid",
                "payment_intent": "pi_test_1",
                "metadata": {"order_id": "order-1"},
            }},
        }
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            "order_id": "order-1",
            "customer_id": "customer-1",
            "payment_status": "PENDING",
        }]
        mock_client.return_value = client

        handle_webhook(b"signed-payload", "signature")

        client.rpc.assert_called_once_with("finalize_stripe_payment", {
            "p_event_id": "evt_test_1",
            "p_event_type": "checkout.session.completed",
            "p_order_id": "order-1",
            "p_session_id": "cs_test_1",
            "p_payment_intent": "pi_test_1",
        })
