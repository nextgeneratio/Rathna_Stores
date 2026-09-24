-- Phase 4 schema additions.
-- Apply only after reviewing in the Supabase SQL editor. Staff issue/revoke
-- operations remain server-side; customers can read only their own active offers.

ALTER TABLE public.payments ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE public.payments ADD COLUMN IF NOT EXISTS provider_checkout_session_id TEXT NULL;
ALTER TABLE public.payments ADD COLUMN IF NOT EXISTS provider_payment_intent_id TEXT NULL;
ALTER TABLE public.payments ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_provider_checkout_session
ON public.payments(provider, provider_checkout_session_id)
WHERE provider_checkout_session_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_idempotency_key
ON public.payments(idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.customer_offers (
  offer_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id UUID NOT NULL REFERENCES public.customers(customer_id) ON DELETE CASCADE,
  rule_version TEXT NOT NULL,
  segment TEXT NOT NULL,
  product_id UUID NULL REFERENCES public.products(product_id) ON DELETE RESTRICT,
  category_id UUID NULL REFERENCES public.categories(category_id) ON DELETE RESTRICT,
  condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  benefit_type TEXT NOT NULL CHECK (benefit_type IN ('PERCENT','AMOUNT','FREE_DELIVERY')),
  benefit_value NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (benefit_value >= 0),
  starts_at TIMESTAMPTZ NOT NULL,
  ends_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','REDEEMED','EXPIRED','REVOKED')),
  single_use BOOLEAN NOT NULL DEFAULT TRUE,
  creation_source TEXT NOT NULL CHECK (creation_source IN ('STAFF','STAFF_REVIEWED_AI_DRAFT')),
  copy_text TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT customer_offers_valid_window CHECK (ends_at > starts_at),
  CONSTRAINT customer_offers_target_present CHECK (product_id IS NOT NULL OR category_id IS NOT NULL OR condition_json <> '{}'::jsonb)
);

CREATE INDEX IF NOT EXISTS idx_customer_offers_customer_status_window
ON public.customer_offers(customer_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_customer_offers_product_id ON public.customer_offers(product_id);
CREATE INDEX IF NOT EXISTS idx_customer_offers_category_id ON public.customer_offers(category_id);

CREATE TABLE IF NOT EXISTS public.offer_redemptions (
  redemption_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  offer_id UUID NOT NULL REFERENCES public.customer_offers(offer_id) ON DELETE RESTRICT,
  customer_id UUID NOT NULL REFERENCES public.customers(customer_id) ON DELETE RESTRICT,
  order_id UUID NOT NULL REFERENCES public.orders(order_id) ON DELETE RESTRICT,
  redeemed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (offer_id),
  UNIQUE (order_id, offer_id)
);
CREATE INDEX IF NOT EXISTS idx_offer_redemptions_customer_id ON public.offer_redemptions(customer_id);

CREATE TABLE IF NOT EXISTS public.stripe_webhook_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processing_status TEXT NOT NULL CHECK (processing_status IN ('PROCESSED','IGNORED','FAILED')),
  order_id UUID NULL REFERENCES public.orders(order_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_order_id ON public.stripe_webhook_events(order_id);

-- The paid transition must be one transaction: duplicate webhook delivery,
-- stock decrement, cart clearing, payment/order state, and history all move
-- together. The service calls this only after verifying Stripe's signature and
-- paid state. The function is intentionally not exposed to browser clients.
CREATE OR REPLACE FUNCTION public.finalize_stripe_payment(
  p_event_id TEXT,
  p_event_type TEXT,
  p_order_id UUID,
  p_session_id TEXT,
  p_payment_intent TEXT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_order public.orders%ROWTYPE;
  v_item RECORD;
  v_stock INTEGER;
  v_event_status TEXT;
BEGIN
  INSERT INTO public.stripe_webhook_events(event_id, event_type, processing_status, order_id)
  VALUES (p_event_id, p_event_type, 'FAILED', p_order_id)
  ON CONFLICT (event_id) DO NOTHING;

  SELECT processing_status INTO v_event_status
  FROM public.stripe_webhook_events
  WHERE event_id = p_event_id
  FOR UPDATE;
  IF v_event_status = 'PROCESSED' THEN
    RETURN;
  END IF;

  SELECT * INTO v_order FROM public.orders WHERE order_id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Order not found';
  END IF;
  IF v_order.payment_status = 'PAID' THEN
    UPDATE public.stripe_webhook_events SET processing_status = 'PROCESSED', processed_at = now() WHERE event_id = p_event_id;
    RETURN;
  END IF;

  FOR v_item IN SELECT product_id, quantity FROM public.order_items WHERE order_id = p_order_id LOOP
    SELECT stock_quantity INTO v_stock FROM public.products WHERE product_id = v_item.product_id FOR UPDATE;
    IF NOT FOUND OR v_stock < v_item.quantity THEN
      RAISE EXCEPTION 'Insufficient stock for paid order';
    END IF;
    UPDATE public.products SET stock_quantity = stock_quantity - v_item.quantity, updated_at = now() WHERE product_id = v_item.product_id;
  END LOOP;

  UPDATE public.payments
  SET payment_status = 'SUCCEEDED', provider_payment_intent_id = p_payment_intent, transaction_id = p_payment_intent, paid_at = now(), updated_at = now()
  WHERE provider_checkout_session_id = p_session_id;
  UPDATE public.orders SET order_status = 'CONFIRMED', payment_status = 'PAID', order_date = now(), updated_at = now() WHERE order_id = p_order_id;
  DELETE FROM public.cart_items WHERE cart_id IN (SELECT cart_id FROM public.carts WHERE customer_id = v_order.customer_id);
  INSERT INTO public.order_status_history(order_id, status, note) VALUES (p_order_id, 'CONFIRMED', 'Verified Stripe test payment');
  UPDATE public.stripe_webhook_events SET processing_status = 'PROCESSED', order_id = COALESCE(order_id, p_order_id), processed_at = now() WHERE event_id = p_event_id;
END;
$$;

REVOKE ALL ON FUNCTION public.finalize_stripe_payment(TEXT, TEXT, UUID, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_stripe_payment(TEXT, TEXT, UUID, TEXT, TEXT) TO service_role;

ALTER TABLE public.customer_offers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "customers read own active offers" ON public.customer_offers;
CREATE POLICY "customers read own active offers"
ON public.customer_offers FOR SELECT TO authenticated
USING (
  status = 'ACTIVE'
  AND starts_at <= now()
  AND ends_at > now()
  AND EXISTS (
    SELECT 1 FROM public.customers AS c
    WHERE c.customer_id = customer_offers.customer_id
      AND c.auth_user_id = (SELECT auth.uid())
      AND c.is_active = TRUE
  )
);

ALTER TABLE public.offer_redemptions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "customers read own offer redemptions" ON public.offer_redemptions;
CREATE POLICY "customers read own offer redemptions"
ON public.offer_redemptions FOR SELECT TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.customers AS c
    WHERE c.customer_id = offer_redemptions.customer_id
      AND c.auth_user_id = (SELECT auth.uid())
      AND c.is_active = TRUE
  )
);

-- No browser-facing policy is created for writes. Staff/server operations must
-- use a trusted server boundary and validate eligibility atomically at checkout.
