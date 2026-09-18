import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from customers.services import get_authenticated_customer_id, get_customer_profile

from .services import ChatbotError, ask_assistant


@require_POST
def chat(request):
    try:
        payload = json.loads(request.body or "{}")
        history = payload.get("messages", [])
        if not isinstance(history, list):
            raise ValueError
        history = history[-12:]
        if not all(isinstance(item, dict) for item in history):
            raise ValueError

        customer_name = ""
        customer_id = get_authenticated_customer_id(request.session)
        if customer_id:
            customer = get_customer_profile(customer_id)
            customer_name = (customer or {}).get("first_name", "")
        answer = ask_assistant(history, customer_name)
        return JsonResponse({"message": answer})
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({"error": "Please send a valid chat message."}, status=400)
    except ChatbotError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
