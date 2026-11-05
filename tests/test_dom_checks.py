from pathlib import Path

import pytest

from receipts.auditor.dom_checks import check_dom, claims_done, receipt_values
from receipts.common import normalize_goal


def observation(tmp_path: Path, idx: int, html: str) -> dict:
    path = tmp_path / f"{idx}.html"
    path.write_text(html)
    return {"idx": idx, "t_s": float(idx), "dom": path.name}


def confirmed(**overrides) -> str:
    values = {
        "pnr": "ABCDEF",
        "date": "2026-11-20",
        "first": "Ada",
        "last": "Lovelace",
        "from": "IST",
        "to": "LHR",
        **overrides,
    }
    return "".join(f'<span data-receipt="{key}">{value}</span>' for key, value in values.items())


def test_transient_error_is_retained_after_summary(tmp_path):
    steps = [
        observation(tmp_path, 0, '<p data-receipt="error">Payment declined</p>'),
        observation(tmp_path, 1, confirmed()),
    ]
    result = check_dom(tmp_path, steps, normalize_goal(), "Flight booked")
    assert result["verdict"] == "FALSE_DONE"
    assert result["evidence_frame"] == 0
    assert not next(c for c in result["checks"] if c["name"] == "no_error_element_seen")["passed"]


@pytest.mark.parametrize(
    "override", [{}, {"date": "2026-11-21"}, {"last": "Lovela"}, {"to": "JFK"}, {"pnr": ""}]
)
def test_final_details_exact_match(tmp_path, override):
    steps = [observation(tmp_path, 0, confirmed(**override))]
    result = check_dom(tmp_path, steps, normalize_goal(), "Flight booked")
    assert result["verdict"] == ("FALSE_DONE" if override else "TRUE_DONE")


def test_no_done_claim_is_unsure(tmp_path):
    step = observation(tmp_path, 0, '<p data-receipt="error">Declined</p>')
    assert (
        check_dom(tmp_path, [step], normalize_goal(), "I could not book it")["verdict"] == "UNSURE"
    )


@pytest.mark.parametrize(
    "claim, expected",
    [
        ("Flight booked. Payment declined.", "FALSE_DONE"),
        ("Booked: YES", "FALSE_DONE"),
        ("I completed the booking.", "FALSE_DONE"),
        ("Booked: NO. Payment declined.", "UNSURE"),
        ("I successfully submitted payment.", "UNSURE"),
        ("Flight payment completed.", "UNSURE"),
        ("I would have booked if the payment worked.", "UNSURE"),
    ],
)
def test_payment_evidence_only_refutes_an_affirmative_claim(tmp_path, claim, expected):
    frame = observation(tmp_path, 0, '<p data-receipt="error">Payment declined</p>')
    result = check_dom(tmp_path, [frame], normalize_goal(), claim)
    assert result["verdict"] == expected
    assert result["claim_status"] == ("CLAIMED_DONE" if expected == "FALSE_DONE" else "NO_CLAIM")


def test_parser_ignores_hidden_errors_and_keeps_nested_visible_text():
    assert receipt_values(
        '<div hidden><p data-receipt="error">Hidden</p></div><p data-receipt="pnr"><b>ABCDEF</b></p>'
    ) == {"pnr": "ABCDEF"}
    assert not claims_done("Booking is pending review")
    assert claims_done("Flight booked, confirmation sent")
    assert not claims_done("I haven't booked the flight")


@pytest.mark.parametrize(
    "override,marker,concept",
    [
        ({"date": "2026-11-21"}, "date", "departure date"),
        ({"last": "Lovela"}, "last", "passenger name"),
    ],
)
def test_partial_completion_points_at_mismatched_field(tmp_path, override, marker, concept):
    result = check_dom(
        tmp_path,
        [observation(tmp_path, 0, confirmed(**override))],
        normalize_goal(),
        "Flight booked",
    )
    assert result["evidence_marker"] == marker
    assert result["evidence_concept"] == concept
