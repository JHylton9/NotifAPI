import hmac
import hashlib
import os
import random
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, HttpUrl, field_validator

app = FastAPI(title="NotifAPI", version="1.0.0")

OUTAGE_TERMS = (
    "outage", "power outage", "service interruption", "service disruption",
    "no service", "restoration", "restored", "emergency outage",
    "scheduled outage", "unplanned outage",
    "without water", "no water", "low water pressure", "water supply disruption",
    "water supply interruption", "disruption in their water supply", "supply disrupted",
    "without electricity", "without power", "power interruption", "load shedding",
)


class Post(BaseModel):
    id: str | None = Field(default=None, pattern=r"^[0-9]{1,30}$")
    account: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=50000)
    url: HttpUrl | None = None

    @field_validator("account")
    @classmethod
    def normalize_account(cls, value: str) -> str:
        value = value.strip().lstrip("@").lower()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Account must be nonempty and contain no control characters")
        return value


def authenticate(authorization: str | None = Header(default=None)) -> None:
    token = os.getenv("WEBHOOK_SECRET", "")
    if not token:
        raise HTTPException(503, "Webhook authentication is not configured")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Invalid webhook token")


def send_email(post: Post, category: str) -> None:
    required = ("DESTINATION_EMAIL", "EMAIL_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
    if any(not os.getenv(key) for key in required):
        raise HTTPException(503, "Email delivery is not configured")
    message = EmailMessage()
    try:
        message["From"] = os.environ["EMAIL_FROM"]
        message["To"] = os.environ["DESTINATION_EMAIL"]
        message["Subject"] = f"[{category.upper()}] Post from {post.account}"
        message.set_content(
            f"Account: {post.account}\n\nCategory: {category.upper()}\n\n"
            f"Post: {post.text}\n\nOriginal Post: {post.url or '(not provided)'}\n"
        )
        port = int(os.getenv("SMTP_PORT", "587"))
        context = ssl.create_default_context()
        client = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        options = {"context": context} if port == 465 else {}
        with client(os.environ["SMTP_HOST"], port, timeout=15, **options) as server:
            if port != 465:
                server.starttls(context=context)
            server.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
            server.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError):
        raise HTTPException(502, "Email delivery failed; retry the webhook") from None


@app.get("/")
def index():
    return {"service": "NotifAPI", "webhook": "/post", "health": "/health", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


def watched_accounts():
    return {item.strip().lstrip("@").lower() for item in os.getenv("WATCHED_ACCOUNTS", "").split(",") if item.strip()}


def category_for(post: Post):
    if any(term in post.text.lower() for term in OUTAGE_TERMS):
        return "outage"
    try:
        rate = float(os.getenv("RANDOM_POST_RATE", "0.05"))
        if not 0 <= rate <= 1:
            raise ValueError
    except ValueError:
        raise HTTPException(503, "RANDOM_POST_RATE must be between 0 and 1") from None
    # Stable sampling for X posts: retries retain the same selection.
    draw = int.from_bytes(hashlib.sha256(post.id.encode()).digest()[:8], "big") / 2**64 if post.id else random.random()
    return "random" if draw < rate else None


@app.post("/post", dependencies=[Depends(authenticate)])
def receive_post(post: Post):
    accounts = watched_accounts()
    if not accounts:
        raise HTTPException(503, "Monitored accounts are not configured")
    if post.account not in accounts:
        return {"forwarded": False, "reason": "account not monitored"}
    category = category_for(post)
    if not category:
        return {"forwarded": False, "category": "ordinary"}
    # Complete delivery before responding: serverless background tasks can be stopped.
    send_email(post, category)
    return {"forwarded": True, "category": category}


def authenticate_cron(authorization: str | None = Header(default=None)):
    token = os.getenv("CRON_SECRET", "")
    if not token:
        raise HTTPException(503, "Scheduler authentication is not configured")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Invalid scheduler token")


@app.get("/cron/poll", dependencies=[Depends(authenticate_cron)])
def poll():
    from collector import run_poll
    return run_poll()


@app.get("/status", dependencies=[Depends(authenticate)])
def status():
    from collector import Redis, missing_configuration
    missing = missing_configuration()
    if missing:
        return {"ready": False, "missing": missing}
    with Redis() as store:
        return {"ready": True, "last_success": store.command("GET", "notifapi:last_success"),
                "last_error": store.command("GET", "notifapi:last_error")}
