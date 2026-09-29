-- Phase 5: simulated distance pricing and customer delivery tracking.
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS delivery_distance_km NUMERIC(10,2) NULL;
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS estimated_delivery_at TIMESTAMPTZ NULL;
CREATE INDEX IF NOT EXISTS idx_orders_estimated_delivery_at ON public.orders(estimated_delivery_at);