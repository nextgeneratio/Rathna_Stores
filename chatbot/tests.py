import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from .services import ChatbotError, ask_assistant


class ChatbotEndpointTests(TestCase):
    @patch("chatbot.views.ask_assistant")
    def test_guest_can_chat(self, mock_assistant):
        mock_assistant.return_value = {"message": "Try our chocolate cake.", "products": []}
        response = self.client.post(
            "/chat/",
            data=json.dumps({"messages": [{"role": "user", "content": "What cake do you recommend?"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Try our chocolate cake.")
        mock_assistant.assert_called_once()

    def test_invalid_history_is_rejected(self):
        response = self.client.post(
            "/chat/",
            data=json.dumps({"messages": "not a list"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class ChatbotServiceTests(TestCase):
    @patch("chatbot.services.httpx.post")
    @patch("chatbot.services.get_catalogue_products")
    @patch("chatbot.services.settings.OPENROUTER_API_KEY", "test-key", create=True)
    def test_service_sends_catalogue_context(self, mock_products, mock_post):
        mock_products.return_value = [{
            "name": "Chocolate Cake",
            "categories": {"name": "Birthday"},
            "effective_price_display": "LKR 3,500",
            "in_stock": True,
        }]
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "Chocolate Cake is available."}}]}
        mock_post.return_value = mock_response

        result = ask_assistant([{"role": "user", "content": "What is available?"}], "Amara")

        self.assertEqual(result["message"], "Chocolate Cake is available.")
        request_payload = mock_post.call_args.kwargs["json"]
        self.assertIn("Chocolate Cake", request_payload["messages"][0]["content"])
        self.assertIn("Amara", request_payload["messages"][0]["content"])

    @patch("chatbot.services.httpx.post", side_effect=Exception("network down"))
    @patch("chatbot.services.settings.OPENROUTER_API_KEY", "test-key", create=True)
    def test_service_hides_provider_failure(self, mock_post):
        with self.assertRaises(ChatbotError):
            ask_assistant([{"role": "user", "content": "Hello"}])
