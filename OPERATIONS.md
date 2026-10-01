# Setup and unattended operation

The service is deployed at https://notifapi.vercel.app. Configuration is incomplete until the credentials below are supplied. A running health endpoint does not prove that collection or email works.

## Import the local file

The Git-ignored `.env` has the two accounts and destination filled in. Fill its blank values, then import it in Vercel → NotifAPI → Settings → Environment Variables → Production. Replace existing values with those in this file, including both generated service secrets. Redeploy once to activate them. Routine scheduled runs do not need redeployment.

1. **Brevo email delivery:** Create a free account at https://app.brevo.com. Under Settings ? Senders, add `ginga9yuki@gmail.com` as the sender and complete the email verification. Under SMTP & API ? SMTP, copy the displayed SMTP login into `SMTP_USERNAME`, then generate an SMTP key and place it in `SMTP_PASSWORD`. Host is `smtp-relay.brevo.com`, port is `587`. This is a Brevo SMTP key, not a Gmail app password or a Brevo API key. Complete any transactional-account activation Brevo requires. Gmail only receives the notifications; no Google account access is required. With a free Gmail sender address, Brevo may replace the sender domain with `brevosend.com`. A custom authenticated domain improves sender identity/deliverability later, but is not part of this setup.
2. **X:** At https://console.x.com create a developer account and app, describe the use case as personal utility outage alerts, and copy its Bearer Token into `X_BEARER_TOKEN`. Purchase API credits and set a spending cap. These are separate from an X Premium subscription. No account password for either utility is needed.
3. **Persistent state:** At https://console.upstash.com create a permanent free Redis database. Copy its REST URL and write-enabled REST token into the two `UPSTASH_REDIS_REST_*` fields. Avoid temporary unclaimed databases; progress must survive deployments.

## Schedule and recovery

Recommended workflow: **Upstash QStash (every five minutes) → NotifAPI on Vercel → read Redis cursor → fetch X posts → filter → Brevo SMTP → save Redis cursor.** QStash is the selected external scheduler because it supports retries, delivery logs, and a dead-letter queue. This is a suitability recommendation, not a measured claim that it has the highest uptime of every provider. Its free plan has no uptime SLA.

QStash has not been activated. In the existing Upstash account, select QStash's free plan and create one schedule with destination `https://notifapi.vercel.app/cron/poll`, cron expression `*/5 * * * *`, HTTP method `GET`, a 300-second timeout, two retries, and destination header `Authorization: Bearer <CRON_SECRET>` using the secret actually deployed in Vercel. If creating through the QStash API, use `Upstash-Forward-Authorization` to forward this header; QStash's own API token is separate. Avoid logging either token. Activate only after the service credentials are configured and a manual collection succeeds.

After a successful QStash-triggered run, remove the `crons` entry from `vercel.json` and redeploy once to disable Vercel's native daily cron. The scheduled (cron) job still exists, but QStash owns it; Vercel hosts only the worker. Leave the existing daily schedule in place until that handover is verified. Delivery logs and retries do not replace an independent stale-success alert.

At five-minute intervals QStash makes 288 scheduled deliveries/day, within its free 1,000-message/day quota. Retries also count; two retries on every run would total 864 attempts/day, before any additional messages or callbacks. No paid upgrade is needed for that schedule. Sources: [QStash pricing](https://upstash.com/pricing/qstash), [schedule configuration](https://upstash.com/docs/qstash/api-reference/schedules/create-a-schedule).

The deployed Vercel cron runs daily at 10:00 UTC (5:00 a.m. Jamaica), with Hobby scheduling imprecision of up to 59 minutes. This is a daily fallback, not prompt outage alerting. For useful outage alerts, use a five-minute schedule: Vercel Pro with `*/5 * * * *`, or an external scheduler calling `GET /cron/poll` with `Authorization: Bearer <CRON_SECRET>`. Do not activate two frequent schedulers. No paid upgrade has been purchased.

The collector reads both account timelines, caches account IDs, and requests posts newer than each stored cursor. The first run starts with the preceding 24 hours, not all historical posts. It includes replies and reposts. It fetches every page in the bounded interval before processing oldest first. Failed sends leave the cursor in place for the next scheduled run; one failing account does not prevent trying the other. The cursor is saved after each successful send or intentional skip. A 10-minute lease prevents overlapping function runs. Work is bounded to leave room before Vercel's 300-second timeout.

State survives redeployments. Sampling is stable by post ID. SMTP cannot guarantee exactly-once delivery: if a server accepts mail but the process dies before storing the cursor, a retry may duplicate it. Direct `/post` submissions remain stateless and require producer-side deduplication. Outage detection reads post text, not text embedded only in images, so image-only notices may be missed. A backlog beyond the API timeline window requires archive recovery; this service is not a guarantee of every utility notice.

## September test

After filling `.env`, install development dependencies and run:

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.venv/Scripts/python scripts/september_test.py
.venv/Scripts/python scripts/september_test.py --send
```

The preview finds an actual accessible September 2026 outage post from each account. `--send` submits one per account through the live webhook with a TEST label in the body. Import the local secret and email settings into Vercel and redeploy before testing. It searches the September window in Jamaica time and does not alter production cursors. Re-running with `--send` sends again. Older posts outside the accessible timeline window may require X full-archive access. API calls incur read charges. SMTP acceptance still requires checking inbox/spam to confirm delivery.

## Monitoring without babysitting

Configure an independent monitor to check authenticated `/status` and alert on `ready: false`, a nonempty `last_error`, or stale `last_success` (over 27 hours for daily polling; about 20 minutes for five-minute polling). Monitoring `/health` alone only checks process uptime. A monitoring service is not yet configured. Use a separate notification channel for failures so a broken SMTP account cannot hide its own failure. Keep X low-balance alerts enabled; use a modest spending cap and controlled auto-recharge if desired. A hard cap or zero credits stops collection.

## Indicative monthly costs (USD, checked September 30, 2026)

| Component | Expected cost and condition |
| --- | --- |
| Vercel Hobby | $0 for personal/noncommercial use within limits; native cron only daily |
| Vercel Pro | From $20/month; permits frequent native cron; usage overages possible |
| X post reads | $0.005 per returned post: 600/month = $3; 3,000 = $15; 12,000 = $60 |
| X user lookups | $0.01 per user resource; caching keeps repeat lookups low |
| Upstash Redis | Free tier: 256 MB and 500,000 commands/month; likely sufficient for two accounts |
| Brevo email delivery | Free plan: 300 emails/day; account activation and sender verification required |
| Resend alternative | Free: 3,000 emails/month, max 100/day; verified sender setup required |
| Upstash QStash scheduling | $0 at 288 scheduled deliveries/day within 1,000/day free quota; retries count; not yet activated |
| Independent monitoring | Provider-dependent; not provisioned |

Example: 100 total posts/day across both accounts costs about $15/month in X reads. Hosting plus X is about $15 on Hobby or $35 on Pro, assuming other services fit free tiers. This is a scenario, not measured account volume. Five-minute polling means approximately 8,640 runs/month and 17,280 timeline requests before pagination. X bills returned resources rather than each empty poll; cursors avoid unnecessary repeat reads. Testing, retries, historical backfill and pricing changes can increase costs.

Sources: [X pricing](https://docs.x.com/x-api/getting-started/pricing), [Vercel pricing](https://vercel.com/pricing), [cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing), [Hobby terms](https://vercel.com/docs/plans/hobby), [Upstash](https://upstash.com/pricing/redis), [Resend](https://resend.com/pricing), [Brevo SMTP](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP), [Brevo sender domains](https://help.brevo.com/hc/en-us/articles/35852083084178-Domain-setup-for-better-email-deliverability), [Brevo free plan](https://help.brevo.com/hc/en-us/articles/208589409-About-Brevo-s-pricing-plans).
