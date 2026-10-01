# NotifAPI

POST a social post to `/post`. All matching outage posts from monitored accounts are emailed; other posts are sampled at 5% by default. A scheduled collector also reads configured X account timelines. See [OPERATIONS.md](OPERATIONS.md) for credentials, unattended operation, September tests and costs.

## Configuration

Set the variables in `.env.example` in Vercel **Settings → Environment Variables** for Production, then redeploy. Use a long random `WEBHOOK_SECRET` and pass it as `Authorization: Bearer <secret>`. `WATCHED_ACCOUNTS` is comma-separated and ignores case and a leading `@`. `RANDOM_POST_RATE` accepts 0–1. SMTP uses STARTTLS on port 587 or implicit TLS on 465. Use a verified sender and your provider's SMTP credentials.

## Request

```sh
curl https://YOUR-DEPLOYMENT.vercel.app/post \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"account":"account1","text":"Power outage in the area","url":"https://example.com/posts/123"}'
```

`url` is optional. Success returns `{"forwarded":true,"category":"outage"}` (or `random`). Skipped posts return `forwarded: false`. Invalid input returns 422, invalid authentication 401, missing configuration 503, and SMTP failure 502. Delivery means the SMTP server accepted the message, not guaranteed inbox arrival. `/health` checks the process only, not email connectivity. `/docs` provides the interactive API schema.

Email is sent before returning success. Configure external webhook producers to retry 502/503 with backoff and deduplicate. Direct `/post` requests remain stateless; scheduled collection uses Redis cursors and a concurrency lock. Sampling stays stable when a numeric `id` is supplied. Retries may duplicate emails if SMTP accepts a message before cursor persistence fails. Keyword matching is case-insensitive substring matching, including restoration and water interruption notices.

## Local development

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

Load the environment variables into your shell, then run:

```sh
.venv/Scripts/python -m uvicorn main:app --reload
.venv/Scripts/python -m pytest -q
```

## Deploy

Vercel automatically detects the root `main.py` FastAPI app:

```sh
npx vercel --prod
```

Set all required environment variables before enabling your producer. With configuration missing, `/post` fails closed while `/health` remains available.
