from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer test-secret"}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("WATCHED_ACCOUNTS", "account1,account2")
    monkeypatch.setenv("RANDOM_POST_RATE", "0.05")


def post(text="Hello", account="account1"):
    return client.post("/post", json={"account": account, "text": text}, headers=HEADERS)


def test_authentication():
    assert client.post("/post", json={"account": "account1", "text": "outage"}).status_code == 401


def test_outage_and_account_normalization():
    with patch("main.send_email") as send:
        response = post("Service RESTORED", " @Account1 ")
        assert response.json() == {"forwarded": True, "category": "outage"}
        assert send.call_args.args[0].account == "account1"
        send.assert_called_once()


def test_unmonitored():
    with patch("main.send_email") as send:
        assert post("outage", "stranger").json()["forwarded"] is False
        send.assert_not_called()


@pytest.mark.parametrize("draw,forwarded", [(0.01, True), (0.05, False), (0.99, False)])
def test_sampling(draw, forwarded):
    with patch("main.random.random", return_value=draw), patch("main.send_email") as send:
        assert post().json()["forwarded"] is forwarded
        assert send.called is forwarded


def test_validation():
    assert post(account="\n").status_code == 422
    assert post(text="").status_code == 422
    assert client.post("/post", json={"account": "account1", "text": "hi", "url": "javascript:alert(1)"}, headers=HEADERS).status_code == 422


def test_missing_email_config(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert post("outage").status_code == 503


def test_smtp_failure(monkeypatch):
    for key in ("DESTINATION_EMAIL", "EMAIL_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.setenv(key, "test@example.com")
    with patch("main.smtplib.SMTP", side_effect=OSError("private details")):
        response = post("outage")
        assert response.status_code == 502
        assert "private details" not in response.text
