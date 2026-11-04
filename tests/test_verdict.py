import pytest

from receipts.auditor.verdict import combine


@pytest.mark.parametrize(
    ("spark_verdict", "dom_verdict", "expected_verdict", "expected_source"),
    [
        ("TRUE_DONE", "TRUE_DONE", "TRUE_DONE", "agreement"),
        ("TRUE_DONE", "FALSE_DONE", "FALSE_DONE", "dom"),
        ("TRUE_DONE", "UNSURE", "UNSURE", "incomplete"),
        ("FALSE_DONE", "TRUE_DONE", "UNSURE", "disagreement"),
        ("FALSE_DONE", "FALSE_DONE", "FALSE_DONE", "dom"),
        ("FALSE_DONE", "UNSURE", "UNSURE", "incomplete"),
        ("UNSURE", "TRUE_DONE", "TRUE_DONE", "dom"),
        ("UNSURE", "FALSE_DONE", "FALSE_DONE", "dom"),
        ("UNSURE", "UNSURE", "UNSURE", "agreement"),
    ],
)
def test_combine_rule_table(
    spark_verdict: str,
    dom_verdict: str,
    expected_verdict: str,
    expected_source: str,
) -> None:
    spark = {
        "verdict": spark_verdict,
        "evidence_frame": 8,
        "rationale": "Spark rationale",
    }
    dom = {
        "verdict": dom_verdict,
        "evidence_frame": 3,
        "evidence_t_s": 1.25,
        "checks": [{"name": "error_visible", "passed": False}],
        "rationale": "Visible error element",
    }

    result = combine(spark, dom)

    assert result["verdict"] == expected_verdict
    assert result["source"] == expected_source
    if dom_verdict == "FALSE_DONE":
        assert result["evidence_frame"] == 3
        assert result["evidence_t_s"] == 1.25
        assert result["checks"] == dom["checks"]
        assert result["rationale"] == "Deterministic failure: Visible error element"


@pytest.mark.parametrize(
    ("spark", "dom"),
    [
        ({"verdict": "DONE"}, {"verdict": "TRUE_DONE"}),
        ({"verdict": "TRUE_DONE"}, {"verdict": "MAYBE"}),
    ],
)
def test_unknown_verdict_is_rejected(spark: dict, dom: dict) -> None:
    with pytest.raises(ValueError, match="Unknown verdict"):
        combine(spark, dom)
