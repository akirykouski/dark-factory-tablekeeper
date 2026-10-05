"""S2-26..S2-48: browser product, driven in headless Chromium at 375 px and 1280 px."""
import copy
import os
import pathlib
import re
import uuid
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import BASE_URL, PAST_THU, THU, FRI, SAT, Api, base_fixture

EVIDENCE = pathlib.Path(__file__).resolve().parents[2] / "evidence" / "stage-2"
EVIDENCE.mkdir(parents=True, exist_ok=True)
T = 8000  # ms


def fx_ui():
    fx = copy.deepcopy(base_fixture())
    r = fx["restaurants"][0]
    r["tables"] = [{"id": "t_1", "label": "Window 1", "capacity": 2},
                   {"id": "t_2", "label": "Garden 2", "capacity": 4},
                   {"id": "t_3", "label": "Booth 3", "capacity": 6}]
    r["combinable"] = [["t_1", "t_2"]]
    return fx


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def api_ui():
    a = Api()
    a.reset(fx_ui())
    yield a
    a.client.close()


def new_page(browser, width=1280):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(T)
    page._external = []
    origin = BASE_URL.rstrip("/")

    def on_req(req):
        if not req.url.startswith(origin) and not req.url.startswith("data:"):
            page._external.append(req.url)
    page.on("request", on_req)
    return page


@pytest.fixture(params=[1280, 375], ids=["desktop", "mobile"])
def page(browser, api_ui, request):
    pg = new_page(browser, request.param)
    pg._width = request.param
    yield pg
    assert pg._external == [], f"external requests: {pg._external}"
    pg.context.close()


@pytest.fixture
def desk(browser, api_ui):
    pg = new_page(browser, 1280)
    pg._width = 1280
    yield pg
    assert pg._external == [], f"external requests: {pg._external}"
    pg.context.close()


def tid(page, t):
    return page.get_by_test_id(t)


def login(page, email="ada@example.com", password="correct horse"):
    page.goto(BASE_URL + "/login")
    tid(page, "login-email").fill(email)
    tid(page, "login-password").fill(password)
    tid(page, "login-submit").click()
    expect(tid(page, "current-user")).to_be_visible()


def search(page, date=THU, party=2, restaurant="r_anker"):
    if urlparse(page.url).path != "/":
        page.goto(BASE_URL + "/")
    tid(page, "restaurant-select").select_option(restaurant)
    tid(page, "date-input").fill(date)
    tid(page, "party-size-input").fill(str(party))
    tid(page, "search-button").click()


def cell(page, table, hhmm):
    return tid(page, f"slot-{table}-{hhmm}")


def no_hscroll(page):
    w = page.evaluate("document.documentElement.scrollWidth")
    assert w <= page.viewport_size["width"], f"horizontal scroll: {w}"


def shot(page, name):
    page.screenshot(path=str(EVIDENCE / f"{name}-{page.viewport_size['width']}.png"),
                    full_page=True)


# --- routes and shell ---------------------------------------------------------------

@pytest.mark.parametrize("route", ["/", "/signup", "/login", "/lookup"])
def test_routes_are_html(api_ui, route):
    r = api_ui.client.get(route)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("route", ["/", "/signup", "/login", "/lookup"])
def test_routes_render_without_hscroll(page, route):
    page.goto(BASE_URL + route)
    page.wait_for_load_state("networkidle")
    no_hscroll(page)
    shot(page, "route" + route.replace("/", "_"))


def test_design_tokens_defined_once(desk):
    desk.goto(BASE_URL + "/")
    n = desk.evaluate("""() => { let n = 0;
        for (const s of document.styleSheets) { let rules; try { rules = s.cssRules } catch (e) { continue }
          for (const r of rules) if (r.selectorText === ':root') n += [...r.style].filter(p => p.startsWith('--')).length }
        return n }""")
    assert n >= 8, f"expected a :root token set (type, spacing, colour), found {n} custom properties"


def test_inputs_have_visible_labels(page):
    for route, ids in [("/signup", ["signup-email", "signup-password", "signup-display-name"]),
                       ("/login", ["login-email", "login-password"]),
                       ("/", ["restaurant-select", "date-input", "party-size-input"]),
                       ("/lookup", ["lookup-reference-input"])]:
        page.goto(BASE_URL + route)
        for i in ids:
            el = tid(page, i)
            expect(el).to_be_visible()
            labels = el.evaluate("e => [...(e.labels || [])].map(l => l.innerText.trim())")
            assert any(labels), f"{route} {i} has no visible <label>"


def test_touch_targets_and_focus(page):
    page.goto(BASE_URL + "/login")
    for i in ["login-email", "login-password", "login-submit"]:
        box = tid(page, i).bounding_box()
        assert box["height"] >= 44, (i, box)
    tid(page, "login-email").focus()
    style = tid(page, "login-email").evaluate(
        "e => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.boxShadow] }")
    assert style[0] != "none" or style[2] != "none", f"no visible focus indicator: {style}"


# --- auth -------------------------------------------------------------------------

def test_signup_flow(page):
    page.goto(BASE_URL + "/signup")
    expect(tid(page, "auth-error")).to_have_count(0)
    tid(page, "signup-email").fill(f"ui{uuid.uuid4().hex[:6]}@example.com")
    tid(page, "signup-password").fill("short")
    tid(page, "signup-display-name").fill("Grace")
    tid(page, "signup-submit").click()
    expect(tid(page, "auth-error")).to_be_visible()
    shot(page, "signup-error")
    tid(page, "signup-password").fill("long enough password")
    tid(page, "signup-submit").click()
    expect(tid(page, "current-user")).to_contain_text("Grace")
    expect(tid(page, "auth-error")).to_have_count(0)


def test_signup_taken_email(desk):
    desk.goto(BASE_URL + "/signup")
    tid(desk, "signup-email").fill("ada@example.com")
    tid(desk, "signup-password").fill("long enough password")
    tid(desk, "signup-display-name").fill("Ada2")
    tid(desk, "signup-submit").click()
    expect(tid(desk, "auth-error")).to_be_visible()


def test_login_error_then_success_and_logout(page):
    page.goto(BASE_URL + "/login")
    expect(tid(page, "auth-error")).to_have_count(0)
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill("wrong password")
    tid(page, "login-submit").click()
    expect(tid(page, "auth-error")).to_be_visible()
    tid(page, "login-password").fill("correct horse")
    tid(page, "login-submit").click()
    expect(tid(page, "current-user")).to_contain_text("Ada")
    for route in ["/", "/lookup", "/login", "/signup"]:
        page.goto(BASE_URL + route)
        expect(tid(page, "current-user")).to_contain_text("Ada")
    shot(page, "signed-in")
    tid(page, "logout-button").click()
    expect(tid(page, "current-user")).to_have_count(0)
    page.goto(BASE_URL + "/")
    expect(tid(page, "current-user")).to_have_count(0)


# --- grid --------------------------------------------------------------------------

def test_grid_matches_api(page, api_ui):
    tok = api_ui.login("bob@example.com", "battery staple")
    api_ui.book(tok, table_id="t_2", local=f"{THU}T19:00")
    login(page)
    search(page, THU, 2)
    expect(tid(page, "availability-grid")).to_be_visible()
    slots = api_ui.avail("r_anker", THU, 2).json()["slots"]
    for s in slots:
        hh = s["starts_at_local"][11:]
        for t in ["t_1", "t_2", "t_3"]:
            want = "true" if t in s["available_table_ids"] else "false"
            expect(cell(page, t, hh)).to_have_attribute("data-available", want)
    no_hscroll(page)
    shot(page, "grid")
    # party size 3: t_1 (capacity 2) is false everywhere
    search(page, THU, 3)
    expect(cell(page, "t_1", "18:00")).to_have_attribute("data-available", "false")
    expect(cell(page, "t_3", "18:00")).to_have_attribute("data-available", "true")


def test_grid_human_labels(desk, api_ui):
    search(desk, THU, 2)
    grid = tid(desk, "availability-grid")
    expect(grid).to_contain_text("Window 1")
    expect(grid).to_contain_text("Garden 2")
    opts = tid(desk, "restaurant-select").locator("option")
    texts = [o.inner_text() for o in opts.all()]
    assert any("Zum Anker" in t for t in texts)


def test_no_slots(page):
    search(page, SAT, 2)
    expect(tid(page, "no-slots")).to_be_visible()
    expect(tid(page, "availability-grid").locator("[data-testid^='slot-']")).to_have_count(0)
    shot(page, "no-slots")


def test_signed_out_click(desk):
    search(desk, THU, 2)
    cell(desk, "t_1", "19:00").click()
    try:
        expect(tid(desk, "auth-error")).to_be_visible(timeout=3000)
    except AssertionError:
        assert "/login" in desk.url


def test_unavailable_click_does_nothing(desk, api_ui):
    tok = api_ui.login("bob@example.com", "battery staple")
    api_ui.book(tok, table_id="t_2", local=f"{THU}T19:00")
    login(desk)
    search(desk, THU, 2)
    c = cell(desk, "t_2", "19:00")
    expect(c).to_have_attribute("data-available", "false")
    c.click(force=True)
    desk.wait_for_timeout(500)
    expect(tid(desk, "booking-form")).to_have_count(0)


def test_combination_cells(desk, api_ui):
    login(desk)
    search(desk, THU, 5)
    expect(cell(desk, "t_1+t_2", "19:00")).to_have_attribute("data-available", "true")
    expect(cell(desk, "t_1", "19:00")).to_have_attribute("data-available", "false")
    shot(desk, "grid-combo")
    cell(desk, "t_1+t_2", "19:00").click()
    expect(tid(desk, "booking-summary")).to_contain_text("Window 1")
    expect(tid(desk, "booking-summary")).to_contain_text("Garden 2")
    expect(tid(desk, "booking-summary")).to_contain_text("19:00")
    expect(tid(desk, "booking-party-size")).to_have_value("5")
    tid(desk, "booking-submit").click()
    expect(tid(desk, "confirmation-reference")).to_have_text(re.compile(r"^[A-Z0-9]{6,12}$"))
    expect(tid(desk, "confirmation-tables")).to_contain_text("Window 1")
    expect(tid(desk, "confirmation-tables")).to_contain_text("Garden 2")
    ref = tid(desk, "confirmation-reference").inner_text()
    tok = api_ui.login()
    assert api_ui.get(f"/reservations/{ref}", token=tok).json()["table_ids"] == ["t_1", "t_2"]
    # the grid refreshed after the write
    expect(cell(desk, "t_1+t_2", "19:00")).to_have_attribute("data-available", "false")


# --- booking -------------------------------------------------------------------------

def test_book_confirm_and_resubmit(page, api_ui):
    login(page)
    search(page, THU, 2)
    keys = []
    page.on("request", lambda r: keys.append(r.headers.get("idempotency-key"))
            if r.method == "POST" and r.url.endswith("/reservations") else None)
    cell(page, "t_2", "19:00").click()
    expect(tid(page, "booking-form")).to_be_visible()
    expect(tid(page, "booking-summary")).to_contain_text("Garden 2")
    expect(tid(page, "booking-summary")).to_contain_text("19:00")
    expect(tid(page, "booking-party-size")).to_have_value("2")
    tid(page, "booking-submit").click()
    ref_el = tid(page, "confirmation-reference")
    expect(ref_el).to_have_text(re.compile(r"^[A-Z0-9]{6,12}$"))
    ref = ref_el.inner_text()
    expect(tid(page, "confirmation-details")).to_contain_text("Zum Anker")
    expect(tid(page, "confirmation-details")).to_contain_text("Garden 2")
    expect(tid(page, "confirmation-details")).to_contain_text("19:00")
    expect(tid(page, "booking-form")).to_be_visible()
    expect(tid(page, "booking-error")).to_have_count(0)
    no_hscroll(page)
    shot(page, "confirmation")
    # grid refreshed
    expect(cell(page, "t_2", "19:00")).to_have_attribute("data-available", "false")
    # unchanged resubmit -> same reference, same key, no second booking
    tid(page, "booking-submit").click()
    page.wait_for_timeout(700)
    expect(tid(page, "confirmation-reference")).to_have_text(ref)
    expect(tid(page, "booking-error")).to_have_count(0)
    tok = api_ui.login()
    assert len(api_ui.get("/reservations", token=tok).json()["reservations"]) == 1
    assert len(keys) == 2 and keys[0] and keys[0] == keys[1], keys
    # change a field -> new request (new key)
    tid(page, "booking-party-size").fill("3")
    tid(page, "booking-submit").click()
    expect(tid(page, "booking-error")).to_be_visible()
    assert len(keys) == 3 and keys[2] != keys[0]


def test_conflict_409(page, api_ui):
    login(page)
    search(page, THU, 2)
    cell(page, "t_2", "19:00").click()
    expect(tid(page, "booking-form")).to_be_visible()
    tid(page, "booking-party-size").fill("3")
    tok = api_ui.login("bob@example.com", "battery staple")
    api_ui.book(tok, table_id="t_2", local=f"{THU}T19:30")
    tid(page, "booking-submit").click()
    expect(tid(page, "booking-error")).to_be_visible()
    expect(tid(page, "confirmation")).to_have_count(0)
    expect(tid(page, "booking-form")).to_be_visible()
    expect(tid(page, "booking-party-size")).to_have_value("3")
    expect(cell(page, "t_2", "19:00")).to_have_attribute("data-available", "false")
    shot(page, "conflict")


def test_lost_response_then_retry(page, api_ui):
    login(page)
    search(page, THU, 2)
    cell(page, "t_3", "20:00").click()
    keys = []

    def drop(route):
        if route.request.method == "POST":
            keys.append(route.request.headers.get("idempotency-key"))
            route.fetch()          # the server commits the booking ...
            route.abort("failed")  # ... but the browser never sees the response
        else:
            route.continue_()
    page.route("**/reservations", drop)
    tid(page, "booking-submit").click()
    expect(tid(page, "booking-uncertain")).to_be_visible()
    assert tid(page, "booking-uncertain").inner_text().strip()
    expect(tid(page, "booking-error")).to_have_count(0)
    expect(tid(page, "confirmation")).to_have_count(0)
    shot(page, "uncertain")
    tok = api_ui.login()
    server = api_ui.get("/reservations", token=tok).json()["reservations"]
    assert len(server) == 1
    page.unroute("**/reservations")
    seen = []
    page.on("request", lambda r: seen.append(r.headers.get("idempotency-key"))
            if r.method == "POST" and r.url.endswith("/reservations") else None)
    tid(page, "booking-submit").click()
    expect(tid(page, "confirmation-reference")).to_have_text(server[0]["reference"])
    expect(tid(page, "booking-uncertain")).to_have_count(0)
    expect(tid(page, "booking-error")).to_have_count(0)
    assert seen == keys
    assert len(api_ui.get("/reservations", token=tok).json()["reservations"]) == 1


def test_lost_response_then_confirmed_rejection(desk, api_ui):
    login(desk)
    search(desk, THU, 2)
    cell(desk, "t_1", "18:00").click()
    desk.route("**/reservations", lambda route: route.abort("failed")
               if route.request.method == "POST" else route.continue_())
    tid(desk, "booking-submit").click()
    expect(tid(desk, "booking-uncertain")).to_be_visible()
    desk.unroute("**/reservations")
    tok = api_ui.login("bob@example.com", "battery staple")
    api_ui.book(tok, table_id="t_1", local=f"{THU}T18:00")
    tid(desk, "booking-submit").click()
    expect(tid(desk, "booking-error")).to_be_visible()
    expect(tid(desk, "booking-uncertain")).to_have_count(0)
    expect(tid(desk, "confirmation")).to_have_count(0)


def test_out_of_order_search(desk, api_ui):
    login(desk)
    search(desk, THU, 2)
    expect(tid(desk, "availability-grid")).to_be_visible()
    held = []

    def hold(route):
        if f"date={THU}" in route.request.url and not held:
            held.append(route)
        else:
            route.continue_()
    desk.route("**/availability*", hold)
    tid(desk, "date-input").fill(THU)
    tid(desk, "search-button").click()          # search A (held)
    desk.wait_for_timeout(300)
    tid(desk, "date-input").fill(FRI)
    tid(desk, "search-button").click()          # search B
    expect(cell(desk, "t_1", "22:00")).to_be_visible()   # Friday-only slot
    assert held
    held[0].continue_()
    desk.wait_for_timeout(1500)
    expect(cell(desk, "t_1", "22:00")).to_be_visible()
    cell(desk, "t_1", "22:00").click()
    expect(tid(desk, "booking-summary")).to_contain_text("22:00")


def test_late_search_does_not_overwrite_form(desk, api_ui):
    login(desk)
    held = []
    desk.route("**/availability*", lambda r: held.append(r) if (
        f"date={THU}" in r.request.url and not held) else r.continue_())
    search(desk, THU, 2)
    desk.wait_for_timeout(300)
    tid(desk, "date-input").fill(FRI)
    tid(desk, "search-button").click()
    cell(desk, "t_2", "22:00").click()
    expect(tid(desk, "booking-summary")).to_contain_text("22:00")
    held[0].continue_()
    desk.wait_for_timeout(1500)
    expect(tid(desk, "booking-summary")).to_contain_text("22:00")
    tid(desk, "booking-submit").click()
    ref = tid(desk, "confirmation-reference").inner_text()
    tok = api_ui.login()
    assert api_ui.get(f"/reservations/{ref}", token=tok).json()["starts_at_local"] == \
        f"{FRI}T22:00"


# --- lookup ------------------------------------------------------------------------

def test_lookup_and_cancel(page, api_ui):
    tok = api_ui.login()
    b = api_ui.book(tok, table_id="t_2", local=f"{THU}T19:00").json()
    login(page)
    page.goto(BASE_URL + "/lookup")
    tid(page, "lookup-reference-input").fill(b["reference"])
    tid(page, "lookup-submit").click()
    expect(tid(page, "reservation-detail")).to_be_visible()
    expect(tid(page, "reservation-status")).to_have_text("confirmed")
    expect(tid(page, "reservation-tables")).to_contain_text("Garden 2")
    no_hscroll(page)
    shot(page, "lookup")
    tid(page, "reservation-cancel-button").click()
    expect(tid(page, "reservation-status")).to_have_text("cancelled")
    expect(tid(page, "reservation-cancel-button")).to_have_count(0)
    assert api_ui.get(f"/reservations/{b['reference']}", token=tok).json()["status"] == \
        "cancelled"
    shot(page, "lookup-cancelled")


def test_lookup_not_found(desk):
    login(desk)
    desk.goto(BASE_URL + "/lookup")
    tid(desk, "lookup-reference-input").fill("NOPE0000")
    tid(desk, "lookup-submit").click()
    expect(tid(desk, "reservation-error")).to_be_visible()
    expect(tid(desk, "reservation-detail")).to_have_count(0)


def test_lookup_other_users_booking_hidden(desk, api_ui):
    tok = api_ui.login("bob@example.com", "battery staple")
    b = api_ui.book(tok, table_id="t_2").json()
    login(desk)
    desk.goto(BASE_URL + "/lookup")
    tid(desk, "lookup-reference-input").fill(b["reference"])
    tid(desk, "lookup-submit").click()
    expect(tid(desk, "reservation-error")).to_be_visible()


def test_lookup_refused_cancel(desk, api_ui):
    tok = api_ui.login()
    b = api_ui.book(tok, table_id="t_2", local=f"{PAST_THU}T19:00").json()
    login(desk)
    desk.goto(BASE_URL + "/lookup")
    tid(desk, "lookup-reference-input").fill(b["reference"])
    tid(desk, "lookup-submit").click()
    tid(desk, "reservation-cancel-button").click()
    expect(tid(desk, "reservation-error")).to_be_visible()
    expect(tid(desk, "reservation-status")).to_have_text("confirmed")
    shot(desk, "lookup-refused")


def test_lookup_pair_tables(desk, api_ui):
    tok = api_ui.login()
    b = api_ui.post("/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"],
                                      "starts_at_local": f"{THU}T19:00", "party_size": 5},
                    token=tok, key="lp").json()
    login(desk)
    desk.goto(BASE_URL + "/lookup")
    tid(desk, "lookup-reference-input").fill(b["reference"])
    tid(desk, "lookup-submit").click()
    expect(tid(desk, "reservation-tables")).to_contain_text("Window 1")
    expect(tid(desk, "reservation-tables")).to_contain_text("Garden 2")


# --- upgrade in the middle of a browser session ------------------------------------

def test_session_and_pending_retry_survive_import(desk, api_ui):
    login(desk)
    search(desk, THU, 2)
    cell(desk, "t_3", "18:00").click()
    desk.route("**/reservations", lambda route: (route.fetch(), route.abort("failed"))
               if route.request.method == "POST" else route.continue_())
    tid(desk, "booking-submit").click()
    expect(tid(desk, "booking-uncertain")).to_be_visible()
    desk.unroute("**/reservations")
    snap = api_ui.export()
    api_ui.reset(fx_ui())          # destination wiped ...
    assert api_ui.import_(snap).status_code == 204   # ... then upgraded from the export
    tid(desk, "booking-submit").click()
    expect(tid(desk, "confirmation-reference")).to_have_text(re.compile(r"^[A-Z0-9]{6,12}$"))
    ref = tid(desk, "confirmation-reference").inner_text()
    expect(tid(desk, "current-user")).to_contain_text("Ada")
    tok = api_ui.login()
    assert [x["reference"] for x in api_ui.get("/reservations", token=tok).json()["reservations"]] == [ref]
    tid(desk, "booking-submit").click()  # still the same
    desk.wait_for_timeout(500)
    expect(tid(desk, "confirmation-reference")).to_have_text(ref)


def test_pair_cells_in_every_slot(desk, api_ui):
    # S2-36: a pair cell is rendered in every slot when the pair's seats fit the party,
    # and reads false where the pair is not in available_options.
    tok = api_ui.login("bob@example.com", "battery staple")
    api_ui.book(tok, table_id="t_1", local=f"{THU}T19:00")
    login(desk)
    search(desk, THU, 2)
    for hh in ["18:00", "19:00", "20:00", "21:30"]:
        expect(cell(desk, "t_1+t_2", hh)).to_have_count(1)
    expect(cell(desk, "t_1+t_2", "19:00")).to_have_attribute("data-available", "false")
    expect(cell(desk, "t_1+t_2", "21:00")).to_have_attribute("data-available", "true")
    # party larger than the pair: no pair cells at all
    search(desk, THU, 7)
    expect(desk.locator("[data-testid^='slot-t_1+t_2-']")).to_have_count(0)
