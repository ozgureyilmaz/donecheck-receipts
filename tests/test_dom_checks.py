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


def test_parser_ignores_hidden_errors_and_keeps_nested_visible_text():
    assert receipt_values(
        '<div hidden><p data-receipt="error">Hidden</p></div><p data-receipt="pnr"><b>ABCDEF</b></p>'
    ) == {"pnr": "ABCDEF"}
    assert not claims_done("Booking is pending review")
    assert claims_done("Flight booked, confirmation sent")
