"""Model disagreement is retained; deterministic failure overrides optimism."""

VERDICTS = {"TRUE_DONE", "FALSE_DONE", "UNSURE"}


def combine(spark: dict, dom: dict) -> dict:
    s, d = spark.get("verdict", "UNSURE"), dom.get("verdict", "UNSURE")
    if s not in VERDICTS or d not in VERDICTS:
        raise ValueError("Unknown verdict")
    if d == "FALSE_DONE":
        return {
            **dom,
            "source": "dom",
            "rationale": "Deterministic failure: " + dom.get("rationale", ""),
        }
    if s == "FALSE_DONE" and d == "TRUE_DONE":
        return {
            **spark,
            "verdict": "UNSURE",
            "source": "disagreement",
            "rationale": "Spark reports failure but DOM checks pass; human review required",
        }
    if s == "UNSURE" and d != "UNSURE":
        return {**dom, "source": "dom"}
    if s == d:
        return {**spark, "source": "agreement"}
    return {
        **spark,
        "verdict": "UNSURE",
        "source": "incomplete",
        "rationale": "Client checks are inconclusive; completion is unverified",
    }
