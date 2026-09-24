-- Phase 4 schema additions.
-- Apply only after reviewing in the Supabase SQL editor. Staff issue/revoke
-- operations remain server-side; customers can read only their own active offers.

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
