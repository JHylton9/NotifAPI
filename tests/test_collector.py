import time
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from collector import fetch_posts, process_posts, run_poll
from main import Post, category_for


def test_nwc_water_language():
    for text in ("Customers will be without water", "low water pressure", "water supply disruption"):
        assert category_for(Post(account="nwcjam", text=text)) == "outage"


def test_sampling_stays_stable(monkeypatch):
    monkeypatch.setenv("RANDOM_POST_RATE", "0.05")
    post = Post(id="123456789", account="nwcjam", text="Ordinary update")
    assert len({category_for(post) for _ in range(50)}) == 1


def test_pagination_is_oldest_first():
    pages = [{"data": [{"id": "30", "text": "outage"}], "meta": {"next_token": "page2"}},
             {"data": [{"id": "20", "text": "outage"}], "meta": {}}]
    with patch("collector.x_get", side_effect=pages):
        posts = fetch_posts(Mock(), "1", {"since_id": "10"}, time.monotonic() + 30)
    assert [p["id"] for p in posts] == ["20", "30"]


def test_failed_delivery_does_not_advance_cursor():
    store = Mock()
    with patch("collector.send_email", side_effect=HTTPException(502, "failed")):
        with pytest.raises(HTTPException):
            process_posts(store, "nwcjam", [{"id": "20", "text": "outage"}], time.monotonic() + 30)
    store.command.assert_not_called()


def test_cursor_follows_successful_send():
    calls = []
    store = Mock()
    store.command.side_effect = lambda *args: calls.append("cursor")
    with patch("collector.send_email", side_effect=lambda *args: calls.append("email")):
        assert process_posts(store, "nwcjam", [{"id": "20", "text": "outage"}], time.monotonic() + 30)["emailed"] == 1
    assert calls == ["email", "cursor"]


def test_overlapping_poll_skips():
    with patch("collector.missing_configuration", return_value=[]), patch("collector.Redis") as redis:
        redis.return_value.__enter__.return_value.command.return_value = None
        assert run_poll() == {"status": "already_running"}


def test_bad_configuration_blocks_poll():
    with patch("collector.missing_configuration", return_value=["X_BEARER_TOKEN"]):
        with pytest.raises(HTTPException) as exc:
            run_poll()
    assert exc.value.status_code == 503
