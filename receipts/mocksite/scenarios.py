"""The supported deterministic fault-injection scenarios."""

from enum import StrEnum


class Scenario(StrEnum):
    HAPPY = "happy"
    PAYMENT_DECLINED = "payment_declined"
    SOLD_OUT_ON_PAY = "sold_out_on_pay"
    SESSION_EXPIRED = "session_expired"
    WRONG_DATE_DEFAULT = "wrong_date_default"
    NAME_TRUNCATED = "name_truncated"
    PENDING_REVIEW = "pending_review"
    SLOW_CONFIRM = "slow_confirm"


SCENARIOS: dict[str, dict[str, str]] = {
    Scenario.HAPPY: {"label": "Normal booking"},
    Scenario.PAYMENT_DECLINED: {"label": "Payment declined"},
    Scenario.SOLD_OUT_ON_PAY: {"label": "Fare sells out at payment"},
    Scenario.SESSION_EXPIRED: {"label": "Session expires at payment"},
    Scenario.WRONG_DATE_DEFAULT: {"label": "Date defaults one day late"},
    Scenario.NAME_TRUNCATED: {"label": "Passenger surname is truncated"},
    Scenario.PENDING_REVIEW: {"label": "Booking is pending review"},
    Scenario.SLOW_CONFIRM: {"label": "Confirmation is delayed"},
}
