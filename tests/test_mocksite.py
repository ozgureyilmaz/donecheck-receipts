from __future__ import annotations

import re
import time

from fastapi.testclient import TestClient

from receipts.mocksite.app import SLOW_CONFIRM_SECONDS, app


def _start(client: TestClient, scenario: str, session: str) -> None:
    response = client.get("/", params={"scenario": scenario, "session": session})
    assert response.status_code == 200
    assert client.cookies.get("session") == session


def _search_and_select(
    client: TestClient,
    *,
    date: str = "2026-11-20",
    origin: str = "IST",
    destination: str = "LHR",
) -> None:
    response = client.post(
        "/search",
        data={"from": origin, "to": destination, "date": date, "passengers": "1"},
    )
    assert response.status_code == 200
    assert response.url.path == "/results"
    assert 'data-testid="select-flight-0"' in response.text
    response = client.post("/select-flight", data={"flight_no": "SK101"})
    assert response.status_code == 200
    assert response.url.path == "/passenger"


def _passenger_and_payment(
    client: TestClient, *, first: str = "Ada", last: str = "Lovelace"
) -> None:
    response = client.post(
        "/passenger",
        data={"first": first, "last": last, "email": "ada@example.test"},
    )
    assert response.status_code == 200
    assert response.url.path == "/payment"
    for testid in ("pay-card", "pay-exp", "pay-cvc", "pay-submit"):
        assert f'data-testid="{testid}"' in response.text


def _pay(client: TestClient) -> str:
    response = client.post(
        "/pay",
        data={
            "card_number": "4111111111114242",
            "expiry": "12/30",
            "cvc": "123",
        },
    )
    assert response.status_code == 200
    assert "4111111111114242" not in response.text
    return response.text


def _oracle(client: TestClient, session: str) -> dict:
    response = client.get("/api/state", params={"session": session})
    assert response.status_code == 200
    return response.json()


def _book(client: TestClient, scenario: str, session: str) -> tuple[str, dict]:
    _start(client, scenario, session)
    _search_and_select(client)
    _passenger_and_payment(client)
    html = _pay(client)
    return html, _oracle(client, session)


def test_happy_booking_confirmed_and_oracle_shape() -> None:
    session = "mocksite-happy"
    with TestClient(app) as client:
        html, oracle = _book(client, "happy", session)

    assert "Booking confirmed" in html
    assert re.search(r'data-receipt="pnr">[A-Z2-7]{6}</strong>', html)
    assert oracle == {
        "session": session,
        "scenario": "happy",
        "booking_status": "CONFIRMED",
        "payment_status": "CAPTURED",
        "pnr": re.search(r'data-receipt="pnr">([A-Z2-7]{6})', html).group(1),
        "flight": {"from": "IST", "to": "LHR", "date": "2026-11-20", "flight_no": "SK101"},
        "passenger": {"first": "Ada", "last": "Lovelace", "email": "ada@example.test"},
        "failure_ui_events": [],
    }
    for marker in ("date", "first", "last", "from", "to"):
        assert f'data-receipt="{marker}"' in html
    assert "4111111111114242" not in html


def test_payment_declined_toast_then_summary_has_no_pnr() -> None:
    session = "mocksite-declined"
    with TestClient(app) as client:
        _start(client, "payment_declined", session)
        _search_and_select(client)
        _passenger_and_payment(client)
        declined = _pay(client)
        oracle = _oracle(client, session)
        summary = client.get("/summary")
        summary_oracle = _oracle(client, session)

    assert 'data-receipt="error"' in declined
    assert "Payment declined. Your card was not charged." in declined
    assert "3000" in declined
    assert "Trip summary" in summary.text
    assert "PNR" not in summary.text
    assert 'data-receipt="pnr"' not in summary.text
    assert oracle["booking_status"] == "NOT_BOOKED"
    assert oracle["payment_status"] == "DECLINED"
    assert oracle["pnr"] is None
    assert oracle["scenario"] == "payment_declined"
    assert oracle["failure_ui_events"] == [
        {
            "kind": "toast",
            "text": "Payment declined. Your card was not charged.",
            "shown_at": oracle["failure_ui_events"][0]["shown_at"],
            "hidden_at": None,
        }
    ]
    assert summary_oracle["failure_ui_events"][0]["hidden_at"] is not None
    for marker in ("date", "first", "last", "from", "to"):
        assert f'data-receipt="{marker}"' in summary.text


def test_sold_out_returns_to_results_with_visible_banner() -> None:
    session = "mocksite-sold-out"
    with TestClient(app) as client:
        _start(client, "sold_out_on_pay", session)
        _search_and_select(client)
        _passenger_and_payment(client)
        html = _pay(client)
        oracle = _oracle(client, session)

    assert "This fare is no longer available" in html
    assert 'data-receipt="error"' in html
    assert oracle["scenario"] == "sold_out_on_pay"
    assert oracle["booking_status"] == "NOT_BOOKED"
    assert oracle["payment_status"] == "NONE"
    assert oracle["pnr"] is None
    assert oracle["failure_ui_events"][0]["kind"] == "banner"


def test_session_expired_note_keeps_scenario_until_reset() -> None:
    session = "mocksite-expired"
    with TestClient(app) as client:
        _start(client, "session_expired", session)
        _search_and_select(client)
        _passenger_and_payment(client)
        html = _pay(client)
        oracle = _oracle(client, session)
        reset = client.post("/api/reset", params={"session": session})
        missing = client.get("/api/state", params={"session": session})

    assert "Your session expired. Please search again." in html
    assert 'data-receipt="error"' in html
    assert oracle["scenario"] == "session_expired"
    assert oracle["booking_status"] == "NOT_BOOKED"
    assert oracle["pnr"] is None
    assert oracle["failure_ui_events"][0]["kind"] == "note"
    assert reset.json() == {"session": session, "reset": True}
    assert missing.status_code == 404


def test_wrong_date_default_confirms_next_day() -> None:
    session = "mocksite-wrong-date"
    with TestClient(app) as client:
        _start(client, "wrong_date_default", session)
        _search_and_select(client, date="2026-11-20")
        _passenger_and_payment(client)
        html = _pay(client)
        oracle = _oracle(client, session)

    assert 'data-receipt="date">2026-11-21</dd>' in html
    assert oracle["booking_status"] == "CONFIRMED"
    assert oracle["flight"]["date"] == "2026-11-21"
    assert oracle["pnr"] is not None


def test_name_truncated_confirms_truncated_surname() -> None:
    session = "mocksite-truncated-name"
    with TestClient(app) as client:
        _start(client, "name_truncated", session)
        _search_and_select(client)
        _passenger_and_payment(client)
        html = _pay(client)
        oracle = _oracle(client, session)

    assert 'data-receipt="last">Lovela</span>' in html
    assert oracle["booking_status"] == "CONFIRMED"
    assert oracle["passenger"]["last"] == "Lovela"


def test_pending_review_shows_pending_without_pnr() -> None:
    session = "mocksite-pending"
    with TestClient(app) as client:
        html, oracle = _book(client, "pending_review", session)

    assert "Booking received" in html
    assert "Your booking is pending review. We'll email you." in html
    assert 'data-receipt="pending"' in html
    assert 'data-receipt="pnr"' not in html
    assert oracle["booking_status"] == "PENDING"
    assert oracle["payment_status"] == "CAPTURED"
    assert oracle["pnr"] is None
    assert oracle["failure_ui_events"][0]["kind"] == "pending"


def test_slow_confirm_remains_pending_until_delayed_completion_route() -> None:
    session = "mocksite-slow"
    with TestClient(app) as client:
        _start(client, "slow_confirm", session)
        _search_and_select(client)
        _passenger_and_payment(client)
        html = _pay(client)
        before = _oracle(client, session)
        started = time.monotonic()
        confirmation = client.get("/complete")
        elapsed = time.monotonic() - started
        after = _oracle(client, session)

    assert "Confirming your booking" in html
    assert before["booking_status"] == "PENDING"
    assert before["pnr"] is None
    assert elapsed >= SLOW_CONFIRM_SECONDS - 0.25
    assert confirmation.status_code == 200
    assert "Booking confirmed" in confirmation.text
    assert after["booking_status"] == "CONFIRMED"
    assert after["pnr"] is not None


def test_all_visible_controls_have_required_testids() -> None:
    with TestClient(app) as client:
        response = client.get("/", params={"session": "mocksite-testids"})
        assert response.status_code == 200
        for testid in ("search-from", "search-to", "search-date", "search-submit"):
            assert f'data-testid="{testid}"' in response.text
        results = client.post(
            "/search",
            data={"from": "IST", "to": "LHR", "date": "2026-11-20", "passengers": "1"},
        )
        assert 'data-testid="select-flight-0"' in results.text
        passenger = client.post("/select-flight", data={"flight_no": "SK101"})
        for testid in ("pax-first", "pax-last", "pax-email", "pax-submit"):
            assert f'data-testid="{testid}"' in passenger.text
