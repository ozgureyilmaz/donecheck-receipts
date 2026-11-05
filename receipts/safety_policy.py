"""Deterministic confirmation rules for the local mock browser."""

from urllib.parse import urlsplit

ALLOWED_CODES = frozenset({"computer_action_requires_confirmation"})
MOCK_HOSTS = frozenset({"127.0.0.1", "localhost"})


def decide_confirmation(check: object, url: str, *, enabled: bool) -> tuple[bool, str]:
    if not enabled:
        return False, "mock_confirmation_flag_required"
    try:
        page = urlsplit(url)
        if (
            page.scheme != "http"
            or page.hostname not in MOCK_HOSTS
            or page.username is not None
            or page.password is not None
        ):
            return False, "non_mock_page"
    except ValueError:
        return False, "non_mock_page"
    if (
        not isinstance(check, dict)
        or not all(isinstance(check.get(field), str) for field in ("id", "code", "message"))
        or not check["id"]
    ):
        return False, "invalid_safety_check"
    if check["code"] not in ALLOWED_CODES:
        return False, "code_not_allowlisted"
    return True, "allowlisted_mock_confirmation"
