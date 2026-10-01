import time
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from collector import fetch_posts, process_posts, run_poll
from main import Post, category_for


def test_nwc_water_language():
    samples = {
        "Customers in Kingston will be without water": "interruption",
        "low water pressure in St. Ann": "interruption",
        "water supply disruption affecting communities in Portland": "interruption",
        "TEMPORARY WATER SUPPLY DISRUPTION IN SPICY GROVE, ST. ANN": "interruption",
        "INTERNAL ELECTRICAL ISSUE INTERRUPTS WATER SUPPLY FOR CUSTOMERS IN ROCK RIVER AND NEARBY AREAS IN CLARENDON": "interruption",
        "RESTORATION UNDERWAY FOR CUSTOMERS IN DINTHILL, DEESIDE, LINSTEAD AND NEARBY AREAS, ST. CATHERINE": "restoration",
        "Power outage affecting customers in Portmore, St. Catherine": "outage",
    }
    for text, expected in samples.items():
        assert category_for(Post(account="nwcjam", text=text)) == expected


def test_locationless_outage_text_is_not_forwarded():
    assert category_for(Post(account="myjpsonline", text="We are aware of the outage")) is None


def test_support_reply_is_not_an_outage():
    text = ("@ktaffe90 Good morning. Thank you for contacting myjpsonline. "
            "We sincerely apologize for any inconvenience caused by the outage you are experiencing. "
            "Our emergency team is aware of the outage and is working to have power restored.")
    assert category_for(Post(account="myjpsonline", text=text, is_reply=True)) is None
    assert category_for(Post(account="myjpsonline", text=text)) is None


def test_location_bearing_reply_is_still_excluded():
    post = Post(account="myjpsonline", is_reply=True,
                text="Customers in sections of Portmore are experiencing a power outage.")
    assert category_for(post) is None


def test_locationless_reply_is_not_sampled(monkeypatch):
    monkeypatch.setenv("RANDOM_POST_RATE", "1")
    assert category_for(Post(account="myjpsonline", text="Thanks for the message", is_reply=True)) is None


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


def test_timeline_excludes_replies_and_retweets_by_default(monkeypatch):
    monkeypatch.delenv("INCLUDE_REPLIES", raising=False)
    with patch("collector.x_get", return_value={"data": [], "meta": {}}) as get:
        fetch_posts(Mock(), "1", {"since_id": "10"}, time.monotonic() + 30)
    assert get.call_args.args[2]["exclude"] == "replies,retweets"


def test_timeline_can_include_replies(monkeypatch):
    monkeypatch.setenv("INCLUDE_REPLIES", "true")
    with patch("collector.x_get", return_value={"data": [], "meta": {}}) as get:
        fetch_posts(Mock(), "1", {"since_id": "10"}, time.monotonic() + 30)
    assert "exclude" not in get.call_args.args[2]


def test_failed_delivery_does_not_advance_cursor():
    store = Mock()
    with patch("collector.send_email", side_effect=HTTPException(502, "failed")):
        with pytest.raises(HTTPException):
            process_posts(store, "nwcjam", [{"id": "20", "text": "outage in Kingston"}], time.monotonic() + 30)
    store.command.assert_not_called()


def test_cursor_follows_successful_send():
    calls = []
    store = Mock()
    store.command.side_effect = lambda *args: calls.append("cursor")
    with patch("collector.send_email", side_effect=lambda *args: calls.append("email")):
        assert process_posts(store, "nwcjam", [{"id": "20", "text": "outage in Kingston"}], time.monotonic() + 30)["emailed"] == 1
    assert calls == ["email", "cursor"]


def test_conversation_id_marks_reply():
    store = Mock()
    item = {"id": "20", "conversation_id": "10",
            "text": "Customers in Kingston face a power outage"}
    with patch("collector.send_email") as send:
        result = process_posts(store, "myjpsonline", [item], time.monotonic() + 30)
    assert result["emailed"] == 0
    send.assert_not_called()


def test_overlapping_poll_skips():
    with patch("collector.missing_configuration", return_value=[]), patch("collector.Redis") as redis:
        redis.return_value.__enter__.return_value.command.return_value = None
        assert run_poll() == {"status": "already_running"}


def test_bad_configuration_blocks_poll():
    with patch("collector.missing_configuration", return_value=["X_BEARER_TOKEN"]):
        with pytest.raises(HTTPException) as exc:
            run_poll()
    assert exc.value.status_code == 503
