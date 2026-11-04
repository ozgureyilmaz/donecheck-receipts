"""FastAPI app for the local SkyLite Air flight-booking exercise."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .scenarios import SCENARIOS, Scenario
from .store import SESSIONS, SessionState, get_or_create, iso_now, record_ui_event

app = FastAPI(title="SkyLite Air", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

FLIGHTS: tuple[dict[str, str], ...] = (
    {"flight_no": "SK101", "depart": "08:15", "arrive": "16:20", "price": "$428"},
    {"flight_no": "SK205", "depart": "12:40", "arrive": "20:45", "price": "$391"},
    {"flight_no": "SK317", "depart": "19:05", "arrive": "03:10", "price": "$456"},
)
DEFAULT_DATE = "2026-11-20"
SESSION_COOKIE = "session"
SLOW_CONFIRM_SECONDS = 6.0


def _session_id(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def _state_for_request(request: Request) -> SessionState | None:
    session_id = _session_id(request)
    return SESSIONS.get(session_id) if session_id else None


def _pnr_for(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii")[:6]


def _home_response(
    request: Request,
    state: SessionState,
    *,
    session_id: str,
    error: str | None = None,
) -> Response:
    response = templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "session_id": session_id,
            "scenario": state.scenario,
            "error": error,
            "origin": "IST",
            "destination": "LHR",
            "travel_date": DEFAULT_DATE,
        },
    )
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return response


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _mark_confirmed(state: SessionState) -> str:
    state.booking_status = "CONFIRMED"
    state.payment_status = "CAPTURED"
    state.pnr = _pnr_for(state.session_id)
    state.confirmation_due_at = None
    return state.pnr


def _confirmation_response(request: Request, state: SessionState) -> HTMLResponse:
    if (
        state.booking_status != "CONFIRMED"
        or state.pnr is None
        or state.flight is None
        or state.passenger is None
    ):
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return templates.TemplateResponse(
        request=request,
        name="confirmation.html",
        context={"state": state},
    )


@app.get("/", response_class=HTMLResponse)
async def search_page(
    request: Request,
    scenario: str | None = None,
    session: str | None = None,
) -> Response:
    session_id = session or _session_id(request) or secrets.token_urlsafe(12)
    if scenario is not None and scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail="Unknown scenario")
    state = get_or_create(session_id, scenario)
    note = None
    if state.scenario == Scenario.SESSION_EXPIRED and state.failure_ui_events:
        latest = state.failure_ui_events[-1]
        if latest["kind"] == "note":
            note = latest["text"]
    return _home_response(request, state, session_id=session_id, error=note)


@app.post("/search", response_class=HTMLResponse)
async def submit_search(
    request: Request,
    origin: str = Form(alias="from"),
    destination: str = Form(alias="to"),
    travel_date: str = Form(alias="date"),
    passengers: int = Form(default=1),
) -> Response:
    state = _state_for_request(request)
    if state is None:
        return _redirect("/")
    if (
        origin not in {"IST", "LHR", "JFK", "CDG"}
        or destination not in {"IST", "LHR", "JFK", "CDG"}
        or origin == destination
    ):
        raise HTTPException(status_code=422, detail="Choose two different listed airports")
    try:
        parsed_date = date.fromisoformat(travel_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Choose a valid travel date") from exc
    if state.scenario == Scenario.WRONG_DATE_DEFAULT:
        parsed_date += timedelta(days=1)
    state.flight = {
        "from": origin,
        "to": destination,
        "date": parsed_date.isoformat(),
        "flight_no": "",
    }
    state.passenger_count = passengers
    state.results_banner = None
    state.selected_flight_no = None
    return _redirect("/results")


@app.get("/results", response_class=HTMLResponse)
async def results_page(request: Request) -> Response:
    state = _state_for_request(request)
    if state is None or state.flight is None:
        return _redirect("/")
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "state": state,
            "flights": FLIGHTS,
            "passengers": state.passenger_count,
            "banner": state.results_banner,
        },
    )


@app.post("/select-flight")
async def select_flight(request: Request, flight_no: str = Form()) -> RedirectResponse:
    state = _state_for_request(request)
    if state is None or state.flight is None:
        return _redirect("/")
    flight = next((candidate for candidate in FLIGHTS if candidate["flight_no"] == flight_no), None)
    if flight is None:
        raise HTTPException(status_code=422, detail="Choose an available flight")
    state.selected_flight_no = flight_no
    state.flight["flight_no"] = flight_no
    return _redirect("/passenger")


@app.get("/passenger", response_class=HTMLResponse)
async def passenger_page(request: Request) -> Response:
    state = _state_for_request(request)
    if state is None or state.flight is None or state.selected_flight_no is None:
        return _redirect("/")
    return templates.TemplateResponse(
        request=request, name="passenger.html", context={"state": state}
    )


@app.post("/passenger")
async def submit_passenger(
    request: Request,
    first: str = Form(),
    last: str = Form(),
    email: str = Form(),
) -> RedirectResponse:
    state = _state_for_request(request)
    if state is None or state.flight is None or state.selected_flight_no is None:
        return _redirect("/")
    first = first.strip()
    last = last.strip()
    if not first or not last or not email.strip():
        raise HTTPException(status_code=422, detail="Complete all passenger details")
    if state.scenario == Scenario.NAME_TRUNCATED:
        last = last[:6]
    state.passenger = {"first": first, "last": last, "email": email.strip()}
    return _redirect("/payment")


@app.get("/payment", response_class=HTMLResponse)
async def payment_page(request: Request) -> Response:
    state = _state_for_request(request)
    if state is None or state.flight is None or state.passenger is None:
        return _redirect("/")
    return templates.TemplateResponse(
        request=request, name="payment.html", context={"state": state}
    )


@app.post("/pay", response_class=HTMLResponse)
async def pay(
    request: Request,
    card_number: str = Form(),
    expiry: str = Form(),
    cvc: str = Form(),
) -> Response:
    """Apply the selected fault without retaining or echoing payment fields."""
    # Read the values to accept normal form submissions, but never save, log, or render them.
    _ = (card_number, expiry, cvc)
    state = _state_for_request(request)
    if state is None or state.flight is None or state.passenger is None:
        return _redirect("/")

    if state.scenario == Scenario.PAYMENT_DECLINED:
        state.booking_status = "NOT_BOOKED"
        state.payment_status = "DECLINED"
        record_ui_event(state, "toast", "Payment declined. Your card was not charged.")
        return templates.TemplateResponse(
            request=request, name="declined.html", context={"state": state}
        )
    if state.scenario == Scenario.SOLD_OUT_ON_PAY:
        state.booking_status = "NOT_BOOKED"
        state.results_banner = "This fare is no longer available"
        record_ui_event(state, "banner", state.results_banner)
        return _redirect("/results")
    if state.scenario == Scenario.SESSION_EXPIRED:
        state.booking_status = "NOT_BOOKED"
        record_ui_event(state, "note", "Your session expired. Please search again.")
        return _redirect("/")
    if state.scenario == Scenario.PENDING_REVIEW:
        state.booking_status = "PENDING"
        state.payment_status = "CAPTURED"
        record_ui_event(state, "pending", "Your booking is pending review. We'll email you.")
        return _redirect("/pending")
    if state.scenario == Scenario.SLOW_CONFIRM:
        state.booking_status = "PENDING"
        state.payment_status = "CAPTURED"
        state.confirmation_due_at = time.monotonic() + SLOW_CONFIRM_SECONDS
        return _redirect("/processing")

    _mark_confirmed(state)
    return _redirect(f"/confirmation/{state.pnr}")


@app.get("/declined", response_class=HTMLResponse)
async def declined_page(request: Request) -> Response:
    state = _state_for_request(request)
    if state is None or state.scenario != Scenario.PAYMENT_DECLINED:
        return _redirect("/")
    return templates.TemplateResponse(
        request=request, name="declined.html", context={"state": state}
    )


@app.get("/summary", response_class=HTMLResponse)
async def summary_page(request: Request) -> Response:
    state = _state_for_request(request)
    if (
        state is None
        or state.scenario != Scenario.PAYMENT_DECLINED
        or state.flight is None
        or state.passenger is None
    ):
        return _redirect("/")
    for event in reversed(state.failure_ui_events):
        if event["kind"] == "toast" and event["hidden_at"] is None:
            event["hidden_at"] = iso_now()
            break
    return templates.TemplateResponse(
        request=request, name="summary.html", context={"state": state}
    )


@app.get("/pending", response_class=HTMLResponse)
async def pending_page(request: Request) -> Response:
    state = _state_for_request(request)
    if state is None or state.scenario != Scenario.PENDING_REVIEW:
        return _redirect("/")
    return templates.TemplateResponse(
        request=request, name="pending.html", context={"state": state}
    )


@app.get("/processing", response_class=HTMLResponse)
async def processing_page(request: Request) -> Response:
    state = _state_for_request(request)
    if (
        state is None
        or state.scenario != Scenario.SLOW_CONFIRM
        or state.confirmation_due_at is None
    ):
        return _redirect("/")
    return templates.TemplateResponse(
        request=request, name="processing.html", context={"state": state}
    )


@app.get("/complete")
async def complete_slow_confirmation(request: Request) -> RedirectResponse:
    state = _state_for_request(request)
    if (
        state is None
        or state.scenario != Scenario.SLOW_CONFIRM
        or state.confirmation_due_at is None
    ):
        return _redirect("/")
    remaining = state.confirmation_due_at - time.monotonic()
    if remaining > 0:
        await asyncio.sleep(remaining)
    pnr = _mark_confirmed(state)
    return _redirect(f"/confirmation/{pnr}")


@app.get("/confirmation/{pnr}", response_class=HTMLResponse)
async def confirmation_page(request: Request, pnr: str) -> HTMLResponse:
    state = _state_for_request(request)
    if state is None or state.pnr != pnr:
        raise HTTPException(status_code=404, detail="Confirmation not found")
    return _confirmation_response(request, state)


@app.get("/api/state")
async def api_state(session: str) -> JSONResponse:
    state = SESSIONS.get(session)
    if state is None:
        return JSONResponse(status_code=404, content={"detail": "Unknown session"})
    return JSONResponse(
        {
            "session": state.session_id,
            "scenario": state.scenario,
            "booking_status": state.booking_status,
            "payment_status": state.payment_status,
            "pnr": state.pnr,
            "flight": state.flight,
            "passenger": state.passenger,
            "failure_ui_events": state.failure_ui_events,
        }
    )


@app.post("/api/reset")
async def api_reset(session: str, request: Request) -> JSONResponse:
    SESSIONS.pop(session, None)
    response = JSONResponse({"session": session, "reset": True})
    if request.cookies.get(SESSION_COOKIE) == session:
        response.delete_cookie(SESSION_COOKIE)
    return response


def _local_host(value: str) -> str:
    """Reject accidental non-loopback binds; this app is only a local fixture."""
    if value != "127.0.0.1":
        raise ValueError("MOCKSITE_HOST must be 127.0.0.1")
    return value


def main() -> None:
    import uvicorn

    host = _local_host(os.getenv("MOCKSITE_HOST", "127.0.0.1"))
    port = int(os.getenv("MOCKSITE_PORT", "8000"))
    uvicorn.run("receipts.mocksite.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
