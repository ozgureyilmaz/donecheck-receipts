import pytest

from receipts.run import _apply_agent_failure


@pytest.mark.parametrize(
    ("dom_verdict", "claim", "failure", "expected_verdict", "expected_source"),
    [
        ("FALSE_DONE", "Flight booked", {"reason": "capture failed"}, "FALSE_DONE", "dom"),
        ("TRUE_DONE", "Flight booked", {"reason": "capture failed"}, "UNSURE", "agent_failure"),
        (
            "UNSURE",
            "Stopped before booking",
            {"reason": "agent stopped"},
            "UNSURE",
            "agent_failure",
        ),
        (
            "FALSE_DONE",
            "No booking was made",
            {"reason": "agent stopped"},
            "UNSURE",
            "agent_failure",
        ),
        ("FALSE_DONE", "Flight booked", None, "FALSE_DONE", "dom"),
    ],
)
def test_agent_failure_preserves_only_decisive_false_done(
    dom_verdict: str,
    claim: str,
    failure: dict | None,
    expected_verdict: str,
    expected_source: str,
) -> None:
    combined = {
        "verdict": dom_verdict,
        "source": "dom" if dom_verdict == "FALSE_DONE" else "agreement",
        "evidence_frame": 4,
        "rationale": "DOM rationale",
    }
    dom = {"verdict": dom_verdict}

    result = _apply_agent_failure(combined, dom, claim, failure)

    assert result["verdict"] == expected_verdict
    assert result["source"] == expected_source
    if expected_source == "agent_failure":
        assert result["rationale"] == failure["reason"]
    else:
        assert result["evidence_frame"] == 4
