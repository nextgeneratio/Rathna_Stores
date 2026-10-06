# PayHere sandbox setup

This project uses PayHere's hosted **Checkout API**. It creates the order on
the server, signs the checkout form on the server, and marks the order paid
only after verifying PayHere's `md5sig` notification.

## 1. Create sandbox credentials

1. Sign in or create an account at [PayHere Sandbox](https://sandbox.payhere.lk/).
2. In **Integrations**, copy the **Merchant ID**.
3. Add the public domain you will use for testing under **Add Domain/App** and
   request approval. Copy the Merchant Secret issued for that domain. Merchant
   Secrets are domain-specific.
4. The notification endpoint must be public; localhost alone cannot receive
   PayHere's server callback. Use a deployed preview or an HTTPS tunnel while
   testing locally.

## 2. Configure the deployment

Set these environment variables in the deployment environment (never commit
the secret):

```dotenv
PAYHERE_MERCHANT_ID=your_sandbox_merchant_id
PAYHERE_MERCHANT_SECRET=your_domain_specific_sandbox_secret
PAYHERE_SANDBOX=true
PAYHERE_NOTIFY_URL=https://your-public-domain.example/payments/payhere/notify/
PAYHERE_RETURN_URL=https://your-public-domain.example/cart/pay/success/
PAYHERE_CANCEL_URL=https://your-public-domain.example/cart/pay/cancel/
```

`PAYHERE_RETURN_URL` and `PAYHERE_CANCEL_URL` are optional; the site derives
them from the incoming request if omitted. Set `PAYHERE_NOTIFY_URL` explicitly
for every deployment so PayHere always calls the correct public HTTPS URL.

## 3. Apply the database migration

Run [`phase4_schema.sql`](phase4_schema.sql) first if it has not already been
applied, then run [`phase6_payhere.sql`](phase6_payhere.sql) in the Supabase SQL
editor. The latter adds an idempotent transaction that prevents duplicate
notifications from decrementing stock twice.

## 4. Test the full flow

1. Create or sign in to a customer account, then add a product to your cart.
2. At checkout, confirm your name, email, and phone number, then enter or
   select an address (PayHere requires a billing address even for pickup).
3. Select **Continue to PayHere** and complete the remaining payment steps on
   PayHere's hosted checkout page using its current sandbox test method.
4. Confirm that PayHere delivered a `POST` to the notification URL and that
   the order becomes `PAID`/`CONFIRMED` only after that notification.

## Going live

Register and approve the production domain in the live PayHere account, use
the new live Merchant Secret for that domain, set `PAYHERE_SANDBOX=false`, and
replace every URL above with the production HTTPS domain. Do not reuse sandbox
credentials in production.

PayHere documents the hosted form fields, request-hash formula, notification
verification formula, and callback requirement in its
[Checkout API guide](https://support.payhere.lk/api-%26-mobile-sdk/checkout-api.I).
