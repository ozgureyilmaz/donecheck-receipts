import pytest

from receipts.safety_policy import decide_confirmation

CHECK = {"id": "check-1", "code": "computer_action_requires_confirmation", "message": "Review"}


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_explicit_mock_confirmation_is_allowed(host):
    assert decide_confirmation(CHECK, f"http://{host}:8000/payment", enabled=True) == (
        True,
        "allowlisted_mock_confirmation",
    )


@pytest.mark.parametrize(
    "url,enabled,check,rule",
    [
        ("http://127.0.0.1:8000/", False, CHECK, "mock_confirmation_flag_required"),
        ("http://127.0.0.1.example/", True, CHECK, "non_mock_page"),
        ("http://localhost@example.org/", True, CHECK, "non_mock_page"),
        ("file://localhost/path", True, CHECK, "non_mock_page"),
        ("http://[broken", True, CHECK, "non_mock_page"),
        (
            "http://localhost/",
            True,
            {**CHECK, "code": "malicious_instructions"},
            "code_not_allowlisted",
        ),
        ("http://localhost/", True, {**CHECK, "id": ""}, "invalid_safety_check"),
    ],
)
def test_other_confirmations_are_declined(url, enabled, check, rule):
    assert decide_confirmation(check, url, enabled=enabled) == (False, rule)
