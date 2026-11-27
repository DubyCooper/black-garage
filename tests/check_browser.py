"""Offline Chromium smoke tests for the current static demo, not a production audit."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]

def scenario(page):
    service_card = page.get_by_role("article").filter(
        has=page.get_by_role("heading", name="Химчистка", exact=True)
    )
    service_card.get_by_role("button", name="Выбрать →").click()
    expect(page.get_by_label("Услуга")).to_have_value("Химчистка")

    slider = page.get_by_role("slider", name="Сравнение до и после")
    for value in (0, 25, 100):
        slider.fill(str(value))
        expect(slider).to_have_value(str(value))
        clip = page.locator("#before").evaluate("(e) => getComputedStyle(e).clipPath")
        expected = f"inset(0px {100 - value}% 0px 0px)"
        # CSSOM may shorten zero-valued sides; compare browser-normalized values.
        normalized = page.locator("#before").evaluate(
            "(e, value) => { const probe = document.createElement('div'); "
            "probe.style.clipPath = value; e.appendChild(probe); "
            "try { return getComputedStyle(probe).clipPath; } finally { probe.remove(); } }",
            expected,
        )
        assert clip == normalized, (clip, normalized)
        assert page.locator("#line").evaluate(
            "(e, value) => Math.abs(parseFloat(getComputedStyle(e).left) "
            "- e.offsetParent.clientWidth * value / 100) <= 1", value
        ), f"Incorrect slider position at {value}%"

    form = page.locator("#bookingForm")
    submit = form.get_by_role("button", name="Оставить заявку →")
    message = page.locator("#message")
    name = page.get_by_label("Ваше имя")
    phone = page.get_by_label("Телефон")
    submit.click()
    assert not form.evaluate("(e) => e.checkValidity()")
    expect(name).to_be_focused()
    expect(message).to_have_text("")
    name.fill("Test User")
    submit.click()
    assert not form.evaluate("(e) => e.checkValidity()")
    expect(phone).to_be_focused()
    expect(message).to_have_text("")
    phone.fill("+79990000000")
    assert form.evaluate("(e) => e.checkValidity()")
    submit.click()
    expect(message).to_be_visible()
    expect(message).to_contain_text("Данные не отправлены")

def main():
    # Bind to loopback only. Test the checked-out files, never a live site.
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for label, viewport in (("desktop", {"width": 1440, "height": 900}),
                                    ("mobile viewport", {"width": 390, "height": 844})):
                context = browser.new_context(viewport=viewport, service_workers="block")
                page = context.new_page()
                errors = []
                failed_local = []
                unexpected = []
                def route_request(route):
                    url = urlsplit(route.request.url)
                    if f"{url.scheme}://{url.netloc}" == origin and route.request.method in ("GET", "HEAD"):
                        route.continue_()
                    else:
                        if route.request.method not in ("GET", "HEAD"):
                            unexpected.append(route.request.method)
                        route.abort()
                context.route("**/*", route_request)
                context.route_web_socket("**/*", lambda ws: ws.close())
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: failed_local.append(response.status)
                        if response.url.startswith(origin + "/") and response.status >= 400
                        and not response.url.endswith("/favicon.ico") else None)
                try:
                    response = page.goto(origin + "/index.html", wait_until="load")
                    assert response and response.ok
                    expect(page.locator("h1").first).to_be_visible()
                    scenario(page)
                    assert page.evaluate(
                        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
                    ), f"Horizontal overflow: {label}"
                    assert not errors, errors
                    assert not failed_local, failed_local
                    assert not unexpected, unexpected
                    print(f"PASS: {label}")
                finally:
                    context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

if __name__ == "__main__":
    main()
