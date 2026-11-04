"""Intentionally optimistic agent; false-done cases are by construction."""

from playwright.async_api import Page

from receipts.agent.recorder import Recorder


async def run_scripted(page: Page, recorder: Recorder, goal: dict) -> str:
    async def fill(testid: str, value: str, select: bool = False) -> None:
        control = page.get_by_test_id(testid)
        if select:
            await control.select_option(value)
        else:
            await control.fill(value)
        stored_value = "[REDACTED]" if testid in {"pay-cvc", "pay-exp"} else value
        await recorder.capture({"type": "fill", "control": testid, "value": stored_value})

    async def click(testid: str) -> None:
        await page.get_by_test_id(testid).click()
        await page.wait_for_load_state("domcontentloaded")
        await recorder.capture({"type": "click", "control": testid})

    await fill("search-from", goal["from"], select=True)
    await fill("search-to", goal["to"], select=True)
    await fill("search-date", goal["date"])
    await click("search-submit")
    await click("select-flight-0")
    for key in ("first", "last", "email"):
        await fill(f"pax-{key}", goal["passenger"][key])
    await click("pax-submit")
    for testid, value in (
        ("pay-card", "4242424242424242"),
        ("pay-exp", "12/28"),
        ("pay-cvc", "123"),
    ):
        await fill(testid, value)
    await click("pay-submit")
    # Observe timers as any browser client could; never consult scenario or oracle.
    if (
        await page.locator('[data-receipt="error"]').count()
        and "declined" in (await page.locator('[data-receipt="error"]').inner_text()).lower()
    ):
        await page.wait_for_url("**/summary", timeout=10000)
        await recorder.capture(
            {"type": "wait", "reason": "transient payment notification disappeared"}
        )
    if (
        "/processing" in page.url
        or await page.locator('[data-testid="processing-spinner"]').count()
    ):
        await page.wait_for_url("**/confirmation/*", timeout=15000)
        await recorder.capture({"type": "wait", "reason": "confirmation finished"})
    return "Flight booked"
