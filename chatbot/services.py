"""Rathna Stores assistant backed by live Supabase catalogue data."""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from django.conf import settings

from cakes.services import get_catalogue_products

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "cohere/north-mini-code:free"
MAX_HISTORY_MESSAGES = 12
MAX_MESSAGE_LENGTH = 1200


class ChatbotError(Exception):
    """A user-safe chatbot failure."""


def _catalogue_context(products: list[dict]) -> str:
    if not products:
        return "The catalogue is temporarily unavailable. Do not invent products or prices."

    lines = []
    for product in products[:40]:
        category = (product.get("categories") or {}).get("name", "Cake")
        price = product.get("effective_price_display") or product.get("price", "price unavailable")
        stock = "in stock" if product.get("in_stock") else "currently out of stock"
        lines.append(f"- {product.get('name', 'Unnamed cake')} | {category} | {price} | {stock}")
    return "Current Rathna Stores catalogue:\n" + "\n".join(lines)


def _build_messages(
    history: list[dict[str, str]],
    customer_name: str = "",
    products: list[dict] | None = None,
) -> list[dict[str, str]]:
    name_context = f"The signed-in customer's first name is {customer_name}." if customer_name else "The shopper is browsing as a guest."
    system = (
        "You are the friendly shopping assistant for Rathna Stores, a handcrafted cake shop in Sri Lanka. "
        "Help shoppers choose cakes, understand availability and prices, and navigate the site. "
        "Be concise, warm, and practical. Use LKR prices from the catalogue context. "
        "Never claim an order was placed, payment was completed, or stock is available unless the context says so. "
        "For custom orders, explain that the feature is coming soon and suggest contacting the shop. "
        f"{name_context}\n\n{_catalogue_context(products or [])}"
    )
    messages = [{"role": "system", "content": system}]
    for item in history[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        content = item.get("content", "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:MAX_MESSAGE_LENGTH]})
    return messages


def ask_assistant(history: list[dict[str, str]], customer_name: str = "") -> dict:
    api_key = getattr(settings, "OPENROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
    api_key = api_key.strip()
    if not api_key:
        raise ChatbotError("The shop assistant is not configured yet.")

    products = get_catalogue_products()
    messages = _build_messages(history, customer_name, products)
    payload = {
        "model": getattr(settings, "OPENROUTER_MODEL", "") or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 500,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8081",
        "X-Title": "Rathna Stores Assistant",
    }

    try:
        response = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        answer = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.error("OpenRouter chatbot request failed: %s", exc)
        raise ChatbotError("The shop assistant is temporarily unavailable.") from exc

    if not answer:
        raise ChatbotError("The shop assistant did not return an answer.")
    answer_lower = answer.lower()
    mentioned_products = [
        product for product in products
        if product.get("name", "").lower() in answer_lower
    ][:8]
    return {"message": answer, "products": mentioned_products}
