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
SUPABASE_SERVICE_ROLE_KEY=your_server_only_supabase_service_role_key
PAYHERE_NOTIFY_URL=https://your-public-domain.example/payments/payhere/notify/
PAYHERE_RETURN_URL=https://your-public-domain.example/cart/pay/success/
PAYHERE_CANCEL_URL=https://your-public-domain.example/cart/pay/cancel/
```

The PayHere notification uses the Supabase service-role key to call the
server-only atomic finalizer. Get it from your Supabase project's API keys and
keep it only in the server environment (for local testing, `.env.local`). Never
use this key in browser code or commit it.

`PAYHERE_RETURN_URL` and `PAYHERE_CANCEL_URL` are optional; the site derives
them from the incoming request if omitted. Set `PAYHERE_NOTIFY_URL` explicitly
for every deployment so PayHere always calls the correct public HTTPS URL.

## 3. Test locally through an HTTPS tunnel

PayHere cannot send its payment notification to `localhost` or `127.0.0.1`.
For local testing, run an HTTPS tunnel to the Django development server. For
example, install `cloudflared` and run these in separate terminals:

```sh
# Terminal 1: from the project directory
. .venv/bin/activate
python manage.py runserver 0.0.0.0:8000

# Terminal 2
cloudflared tunnel --url http://127.0.0.1:8000
```

Copy the `https://…trycloudflare.com` URL printed by `cloudflared`. Add its
hostname (without `https://`) to `DJANGO_ALLOWED_HOSTS`, and add its full
HTTPS origin to `DJANGO_CSRF_TRUSTED_ORIGINS` in `.env.local`. Set the PayHere
URLs there as well. Append these values to any existing host/origin entries
rather than replacing other domains you still use:

```dotenv
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,<tunnel-hostname>
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost,http://127.0.0.1,https://<tunnel-hostname>
PAYHERE_NOTIFY_URL=https://<tunnel-hostname>/payments/payhere/notify/
PAYHERE_RETURN_URL=https://<tunnel-hostname>/cart/pay/success/
PAYHERE_CANCEL_URL=https://<tunnel-hostname>/cart/pay/cancel/
```

Keep the tunnel running during checkout and restart Django after editing
`.env.local`. A tunnel makes your local development server reachable publicly,
so use it only temporarily with sandbox credentials and non-sensitive data.
Quick-tunnel hostnames can change; if yours changes, update the host/origin and
PayHere URL settings and add/approve the new domain in PayHere Sandbox, then
use the Merchant Secret issued for that domain. Do not commit `.env.local` or
secrets.

## 4. Apply the database migration

Run [`phase4_schema.sql`](phase4_schema.sql) first if it has not already been
applied, then run [`phase6_payhere.sql`](phase6_payhere.sql) in the Supabase SQL
editor. If it was applied previously, run the updated script again to replace
the finalizer function. The idempotent transaction marks verified PayHere
orders paid/confirmed, reduces stock, removes the purchased products from the
customer's cart, updates total spend, and prevents duplicate notifications from
repeating those changes.

## 5. Test the full flow

1. Create or sign in to a customer account, then add a product to your cart.
2. At checkout, confirm your name, email, and phone number, then enter or
   select an address (PayHere requires a billing address even for pickup).
3. Select **Continue to PayHere** and complete the remaining payment steps on
   PayHere's hosted checkout page using its current sandbox test method.
4. Confirm that PayHere delivered a `POST` to the notification URL and that
   the order becomes `PAID`/`CONFIRMED` only after that notification. PayHere
   orders are finalized automatically; staff receipt review is reserved for
   manual bank-transfer payments.

## Going live

Register and approve the production domain in the live PayHere account, use
the new live Merchant Secret for that domain, set `PAYHERE_SANDBOX=false`, and
replace every URL above with the production HTTPS domain. Do not reuse sandbox
credentials in production.

PayHere documents the hosted form fields, request-hash formula, notification
verification formula, and callback requirement in its
[Checkout API guide](https://support.payhere.lk/api-%26-mobile-sdk/checkout-api.I).
