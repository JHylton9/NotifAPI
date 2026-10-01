"""Find a real September 2026 outage post per account; send only with --send."""
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import x_get
from main import OUTAGE_TERMS, Post, watched_accounts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Email one matching real post per account")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    required = ["X_BEARER_TOKEN", "WATCHED_ACCOUNTS"]
    if args.send:
        required += ["WEBHOOK_SECRET"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        print("Fill these .env values first: " + ", ".join(missing))
        return 1
    with httpx.Client(timeout=15, headers={"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}) as client:
        found = 0
        for account in sorted(watched_accounts()):
            user_id = x_get(client, f"users/by/username/{account}")["data"]["id"]
            # September in Jamaica (UTC-5), end exclusive.
            end = min(datetime(2026, 10, 1, 5, tzinfo=timezone.utc), datetime.now(timezone.utc) - timedelta(seconds=30))
            params = {"start_time": "2026-09-01T05:00:00Z", "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "max_results": 100, "post.fields": "created_at,note_post"}
            for _ in range(32):
                page = x_get(client, f"users/{user_id}/tweets", params)
                match = next((item for item in page.get("data", [])
                              if any(term in item.get("note_post", {}).get("text", item["text"]).lower()
                                     for term in OUTAGE_TERMS)), None)
                if match:
                    post = Post(id=match["id"], account=account,
                                text=match.get("note_post", {}).get("text", match["text"]),
                                url=f"https://x.com/{account}/status/{match['id']}")
                    print(f"{account}: {match.get('created_at')} {post.url}")
                    if args.send:
                        post.text = "[TEST: historical September 2026 outage post]\n\n" + post.text
                        response = httpx.post("https://notifapi.vercel.app/post", json=post.model_dump(mode="json"),
                                              headers={"Authorization": f"Bearer {os.environ['WEBHOOK_SECRET']}"}, timeout=90)
                        if response.status_code != 200 or not response.json().get("forwarded"):
                            raise HTTPException(502, f"Live webhook test failed (HTTP {response.status_code}); check deployed configuration")
                        print("Live webhook reports SMTP acceptance of test email.")
                    else:
                        print("Preview only. Run with --send to email this post.")
                    found += 1
                    break
                token = page.get("meta", {}).get("next_token")
                if not token:
                    break
                params["pagination_token"] = token
            else:
                print(f"{account}: timeline limit reached; full archive search may be required.")
        if found != len(watched_accounts()):
            print("Could not find a matching accessible September post for every account.")
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HTTPException as exc:
        print(f"Test stopped: {exc.detail}")
        raise SystemExit(1)
    except httpx.HTTPError:
        print("Test stopped: live webhook connection failed.")
        raise SystemExit(1)
