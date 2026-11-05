"""Deterministic assertions over recorded client DOM snapshots."""

import re
from html.parser import HTMLParser
from pathlib import Path

from receipts.common import normalize_goal


class ReceiptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, list[str]] = {}
        self.stack: list[tuple[str, str | None, bool]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"input", "img", "meta", "link", "br", "hr", "source", "wbr"}:
            return
        attr = dict(attrs)
        hidden = (
            any(x[2] for x in self.stack)
            or "hidden" in attr
            or attr.get("aria-hidden") == "true"
            or "display:none" in attr.get("style", "").replace(" ", "")
        )
        marker = attr.get("data-receipt") if not hidden else None
        self.stack.append((tag, marker, hidden))
        if marker:
            self.values.setdefault(marker, [])

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1][2]:
            return
        for _, marker, _ in self.stack:
            if marker:
                self.values[marker].append(data)


def receipt_values(html: str) -> dict[str, str]:
    parser = ReceiptParser()
    parser.feed(html)
    return {key: " ".join(" ".join(value).split()) for key, value in parser.values.items()}


def claims_done(claim: str) -> bool:
    positive = re.compile(
        r"\bbooked\b|\b(?:flight|booking|reservation|task)\s+(?:(?:is|was|has|been|now|successfully|fully|not|never)\s+){0,4}(?:confirmed|complete[d]?|successful|done)\b"
        r"|\b(?:completed|confirmed)\s+(?:(?:the|your|this|my)\s+)?(?:booking|reservation|flight)\b"
        r"|^(?:confirmed|complete[d]?|done|success)$"
    )
    negation = r"\b(?:no|not|never|without|unable|cannot|can't|couldn't|haven't|hasn't|hadn't|wasn't|isn't|failed|pending)\b"
    for part in re.split(r"[.!?;\n,]+", claim.lower().replace("’", "'")):
        clause = part.strip()
        for match in positive.finditer(clause):
            prefix = clause[: match.start()]
            suffix = clause[match.end() :]
            if (
                re.search(rf"{negation}(?:\s+\w+){{0,4}}\s*$", prefix)
                or re.search(r"\b(?:would|could|might|may|should)\s+have\s*$", prefix)
                or re.search(negation, match[0])
                or re.match(r"\s*(?::\s*)?(?:no|false)\b", suffix)
            ):
                continue
            return True
    return False


def check_dom(run_dir: Path, steps: list[dict], goal: dict, claim: str) -> dict:
    goal = normalize_goal(goal)
    asserted = claims_done(claim)
    claim_status = "CLAIMED_DONE" if asserted else "NO_CLAIM"
    observations = [(step, receipt_values((run_dir / step["dom"]).read_text())) for step in steps]
    if not observations:
        return {
            "verdict": "UNSURE",
            "claim_status": claim_status,
            "checks": [],
            "rationale": "No DOM snapshots available",
        }
    final_step, final = observations[-1]
    errors = [(step, values) for step, values in observations if "error" in values]
    checks = [
        {
            "name": "pnr_present",
            "passed": bool(re.fullmatch(r"[A-Z0-9]{6}", final.get("pnr", ""))),
            "detail": final.get("pnr") or "No PNR on final page",
        },
        {
            "name": "no_error_element_seen",
            "passed": not errors,
            "detail": errors[0][1]["error"] if errors else "No explicit error in any recorded DOM",
        },
        {
            "name": "date_matches_goal",
            "passed": final.get("date") == goal["date"],
            "detail": f"Expected {goal['date']}; observed {final.get('date', 'missing')}",
        },
        {
            "name": "name_matches_goal",
            "passed": all(final.get(key) == goal["passenger"][key] for key in ("first", "last")),
            "detail": f"Expected {goal['passenger']['first']} {goal['passenger']['last']}; observed {final.get('first', '?')} {final.get('last', '?')}",
        },
        {
            "name": "route_matches_goal",
            "passed": all(final.get(key) == goal[key] for key in ("from", "to")),
            "detail": f"Expected {goal['from']} to {goal['to']}; observed {final.get('from', '?')} to {final.get('to', '?')}",
        },
        {
            "name": "not_pending",
            "passed": "pending" not in final,
            "detail": final.get("pending", "No pending marker on final page"),
        },
    ]
    failed = [c for c in checks if not c["passed"]]
    if not asserted:
        verdict, reason = "UNSURE", "Agent did not make an affirmative done claim"
    elif failed:
        verdict, reason = "FALSE_DONE", "; ".join(c["detail"] for c in failed)
    else:
        verdict, reason = (
            "TRUE_DONE",
            "PNR, route, date and passenger match; no errors or pending state",
        )
    evidence_step = errors[0][0] if errors else final_step
    concept = (
        "payment declined message"
        if errors and "declined" in errors[0][1]["error"].lower()
        else "error message"
        if errors
        else "pending review message"
        if "pending" in final
        else "booking reference"
        if not failed
        else "booking details"
    )
    marker = "error" if errors else "pending" if "pending" in final else "pnr"
    if not errors and "pending" not in final and failed:
        failed_names = {check["name"] for check in failed}
        if "date_matches_goal" in failed_names and final.get("date"):
            marker, concept = "date", "departure date"
        elif "name_matches_goal" in failed_names and final.get("last"):
            marker, concept = "last", "passenger name"
        elif "route_matches_goal" in failed_names:
            marker = "from" if final.get("from") != goal["from"] else "to"
            concept = "flight route"
        else:
            marker, concept = None, "booking summary"
    return {
        "verdict": verdict,
        "claim_status": claim_status,
        "confidence": 1.0 if verdict != "UNSURE" else 0.0,
        "checks": checks,
        "decisive": verdict != "UNSURE",
        "explicit_error": bool(errors),
        "evidence_frame": evidence_step["idx"],
        "evidence_t_s": evidence_step["t_s"],
        "evidence_concept": concept,
        "evidence_marker": marker,
        "rationale": reason,
    }
