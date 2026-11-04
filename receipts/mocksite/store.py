"""In-memory per-session state for the mock airline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .scenarios import SCENARIOS, Scenario


@dataclass
class SessionState:
    session_id: str
    scenario: str = Scenario.HAPPY
    booking_status: str = "NONE"
    payment_status: str = "NONE"
    pnr: str | None = None
    flight: dict[str, str] | None = None
    passenger: dict[str, str] | None = None
    selected_flight_no: str | None = None
    passenger_count: int = 1
    results_banner: str | None = None
    failure_ui_events: list[dict[str, Any]] = field(default_factory=list)
    confirmation_due_at: float | None = None


SESSIONS: dict[str, SessionState] = {}


def iso_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def get_or_create(session_id: str, scenario: str | None = None) -> SessionState:
    state = SESSIONS.get(session_id)
    if state is None:
        state = SessionState(session_id=session_id)
        SESSIONS[session_id] = state
    if scenario is not None:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}")
        state.scenario = scenario
    return state


def record_ui_event(state: SessionState, kind: str, text: str) -> None:
    state.failure_ui_events.append(
        {"kind": kind, "text": text, "shown_at": iso_now(), "hidden_at": None}
    )
