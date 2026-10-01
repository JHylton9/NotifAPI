"""Scheduled X collection. Redis stores cursors; failed sends never advance them."""
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

from main import Post, category_for, send_email, watched_accounts

REQUIRED = (
    "X_BEARER_TOKEN", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN",
    "DESTINATION_EMAIL", "EMAIL_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD",
    "WATCHED_ACCOUNTS", "CRON_SECRET", "WEBHOOK_SECRET",
)


def missing_configuration():
    return [key for key in REQUIRED if not os.getenv(key)]


class Redis:
    def __enter__(self):
        self.client = httpx.Client(timeout=10)
        return self

    def __exit__(self, *args):
        self.client.close()

    def command(self, *args):
        try:
            response = self.client.post(
                os.environ["UPSTASH_REDIS_REST_URL"],
                headers={"Authorization": f"Bearer {os.environ['UPSTASH_REDIS_REST_TOKEN']}"},
                json=list(args),
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise ValueError
            return data["result"]
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(503, "Persistent storage unavailable") from None


def x_get(client, path, params=None):
    try:
        response = client.get(f"https://api.x.com/2/{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise ValueError
        return payload
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"X API returned HTTP {exc.response.status_code}; check credentials, credits and rate limits") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "X API response unavailable or incomplete") from None


def fetch_posts(client, user_id, params, deadline):
    """Fetch the full bounded interval before advancing any cursor."""
    params = {"max_results": 100, "post.fields": "created_at,note_post", **params}
    found = {}
    for _ in range(32):
        if time.monotonic() > deadline:
            raise HTTPException(503, "Collection time budget exceeded; progress retained")
        page = x_get(client, f"users/{user_id}/tweets", params)
        for item in page.get("data", []):
            found[item["id"]] = item
        token = page.get("meta", {}).get("next_token")
        if not token:
            return sorted(found.values(), key=lambda item: int(item["id"]))
        params["pagination_token"] = token
    raise HTTPException(503, "Timeline backlog exceeds 3200 posts; archive recovery needed")


def process_posts(store, account, posts, deadline):
    processed = sent = 0
    for item in posts:
        if time.monotonic() > deadline:
            raise HTTPException(503, "Delivery time budget exceeded; progress retained")
        post = Post(id=item["id"], account=account,
                    text=item.get("note_post", {}).get("text", item["text"]),
                    url=f"https://x.com/{account}/status/{item['id']}")
        category = category_for(post)
        if category:
            send_email(post, category)
            sent += 1
        # SMTP acceptance precedes durable acknowledgement; see OPERATIONS.md.
        store.command("SET", f"notifapi:cursor:{account}", item["id"])
        processed += 1
    return {"processed": processed, "emailed": sent}


def run_poll():
    if missing_configuration():
        raise HTTPException(503, "Collector configuration incomplete; check authenticated /status")
    deadline = time.monotonic() + 180
    lock = secrets.token_hex(16)
    with Redis() as store:
        # Lease exceeds the platform's 300-second maximum request duration.
        if not store.command("SET", "notifapi:poll_lock", lock, "NX", "EX", 600):
            return {"status": "already_running"}
        try:
            results, failures = {}, {}
            with httpx.Client(timeout=15, headers={"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}) as client:
                for account in sorted(watched_accounts()):
                    try:
                        user_id = store.command("GET", f"notifapi:user:{account}")
                        if not user_id:
                            user_id = x_get(client, f"users/by/username/{account}")["data"]["id"]
                            store.command("SET", f"notifapi:user:{account}", user_id)
                        cursor = store.command("GET", f"notifapi:cursor:{account}")
                        if cursor:
                            params = {"since_id": cursor}
                        else:
                            # Persist the initial window so failures cannot shift it forward.
                            start = store.command("GET", f"notifapi:start:{account}")
                            if not start:
                                start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                                store.command("SET", f"notifapi:start:{account}", start)
                            params = {"start_time": start}
                        posts = fetch_posts(client, user_id, params, deadline)
                        results[account] = process_posts(store, account, posts, deadline)
                    except HTTPException as exc:
                        failures[account] = exc.detail
            if failures:
                store.command("SET", "notifapi:last_error", json.dumps(failures))
                raise HTTPException(502, {"accounts": results, "errors": failures})
            store.command("SET", "notifapi:last_success", datetime.now(timezone.utc).isoformat())
            store.command("DEL", "notifapi:last_error")
            return {"status": "ok", "accounts": results}
        finally:
            store.command("EVAL", "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", 1, "notifapi:poll_lock", lock)
