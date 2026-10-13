-- PayHere Checkout support. Run this in the Supabase SQL editor after
-- phase4_schema.sql, which adds the generic provider fields to payments.

CREATE TABLE IF NOT EXISTS public.payhere_webhook_events (
  event_id TEXT PRIMARY KEY,
  order_id UUID NOT NULL REFERENCES public.orders(order_id) ON DELETE RESTRICT,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processing_status TEXT NOT NULL CHECK (processing_status IN ('PROCESSED', 'FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_payhere_webhook_events_order_id
ON public.payhere_webhook_events(order_id);

-- The notification is signature-verified by Django before this function is
-- called. This transaction guards against duplicate PayHere retries and makes
-- stock reduction, order confirmation, payment state, and cart clearing atomic.
CREATE OR REPLACE FUNCTION public.finalize_payhere_payment(
  p_event_id TEXT,
  p_order_id UUID,
  p_payment_id TEXT,
  p_amount NUMERIC(12,2)
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_order public.orders%ROWTYPE;
  v_payment public.payments%ROWTYPE;
  v_item RECORD;
  v_stock INTEGER;
  v_event_status TEXT;
BEGIN
  INSERT INTO public.payhere_webhook_events(event_id, order_id, processing_status)
  VALUES (p_event_id, p_order_id, 'FAILED')
  ON CONFLICT (event_id) DO NOTHING;

  SELECT processing_status INTO v_event_status
  FROM public.payhere_webhook_events
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
    UPDATE public.payhere_webhook_events
    SET processing_status = 'PROCESSED', processed_at = now()
    WHERE event_id = p_event_id;
    RETURN;
  END IF;

  SELECT * INTO v_payment
  FROM public.payments
  WHERE provider = 'payhere'
    AND provider_checkout_session_id = p_order_id::TEXT
  FOR UPDATE;
  IF NOT FOUND OR v_payment.payment_status <> 'PENDING' THEN
    RAISE EXCEPTION 'Pending PayHere payment not found';
  END IF;
  IF v_payment.amount <> p_amount OR v_order.total_amount <> p_amount THEN
    RAISE EXCEPTION 'PayHere payment amount does not match order';
  END IF;

  FOR v_item IN SELECT product_id, quantity FROM public.order_items WHERE order_id = p_order_id LOOP
    SELECT stock_quantity INTO v_stock FROM public.products WHERE product_id = v_item.product_id FOR UPDATE;
    IF NOT FOUND OR v_stock < v_item.quantity THEN
      RAISE EXCEPTION 'Insufficient stock for paid order';
    END IF;
    UPDATE public.products
    SET stock_quantity = stock_quantity - v_item.quantity, updated_at = now()
    WHERE product_id = v_item.product_id;
  END LOOP;

  UPDATE public.payments
  SET payment_status = 'SUCCEEDED', provider_payment_intent_id = p_payment_id,
      transaction_id = p_payment_id, paid_at = now(), updated_at = now()
  WHERE payment_id = v_payment.payment_id;
  UPDATE public.orders
  SET order_status = 'CONFIRMED', payment_status = 'PAID', order_date = now(), updated_at = now()
  WHERE order_id = p_order_id;
  UPDATE public.customers
  SET total_spend = COALESCE(total_spend, 0) + v_order.total_amount, updated_at = now()
  WHERE customer_id = v_order.customer_id;
  DELETE FROM public.cart_items AS cart_item
  USING public.carts AS cart
  WHERE cart_item.cart_id = cart.cart_id
    AND cart.customer_id = v_order.customer_id
    AND cart_item.product_id IN (
      SELECT product_id FROM public.order_items WHERE order_id = p_order_id
    );
  INSERT INTO public.order_status_history(order_id, status, note)
  VALUES (p_order_id, 'CONFIRMED', 'Verified PayHere payment');
  UPDATE public.payhere_webhook_events
  SET processing_status = 'PROCESSED', processed_at = now()
  WHERE event_id = p_event_id;
END;
$$;

REVOKE ALL ON FUNCTION public.finalize_payhere_payment(TEXT, UUID, TEXT, NUMERIC) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_payhere_payment(TEXT, UUID, TEXT, NUMERIC) TO service_role;
