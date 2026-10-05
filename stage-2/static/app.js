// Tablekeeper browser client: a tiny router plus four screens.
const TOKEN_KEY = "tk_token";
const NAME_KEY = "tk_name";

const S = {
  token: localStorage.getItem(TOKEN_KEY),
  name: localStorage.getItem(NAME_KEY),
  restaurants: null,
  details: {},
  seq: 0,          // search sequence; only the latest response may paint
  last: null,      // latest requested search {rid, date, party}
  shown: null,     // search the visible grid belongs to
  booking: null,   // open booking form state
  gridCtx: null,
  pageUi: null,
};

// ---------- helpers ----------
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "testid") e.setAttribute("data-testid", v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "k-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

class NetworkError extends Error {}

async function api(method, path, body, extraHeaders) {
  const headers = { Accept: "application/json", ...(extraHeaders || {}) };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (S.token) headers.Authorization = "Bearer " + S.token;
  let res, text;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    text = await res.text();
  } catch (e) {
    throw new NetworkError(String(e));
  }
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  return { status: res.status, data };
}

const MESSAGES = {
  table_unavailable: "Sorry, that table was just taken. Availability has been refreshed. Please pick another table or time.",
  party_exceeds_capacity: "That party is too large for the selected table.",
  outside_opening_hours: "The restaurant is not open at that time.",
  not_on_slot_grid: "That start time is not offered.",
  invalid_local_time: "That local time does not exist on this date.",
  combination_not_allowed: "Those tables cannot be combined.",
  validation_failed: "Please check your details: party size must be a whole number of at least 1.",
  unauthenticated: "Please sign in to continue.",
  not_found: "We could not find that.",
  cutoff_passed: "It is too close to the start time to change this booking.",
  email_taken: "That email is already registered. Try signing in instead.",
  idempotency_key_reuse: "That request was already used with different details. Please try again.",
};
function humanError(res, fallback) {
  const code = res && res.data && res.data.error && res.data.error.code;
  return MESSAGES[code] || fallback || "Something went wrong. Please try again.";
}

function msg(kind, testid, ...kids) {
  return h("div", { class: "msg " + kind, testid, role: kind === "error" ? "alert" : "status" },
    h("div", { class: "msg-body" }, kids));
}

function setSession(token, name) {
  S.token = token; S.name = name;
  if (token) { localStorage.setItem(TOKEN_KEY, token); localStorage.setItem(NAME_KEY, name || ""); }
  else { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(NAME_KEY); }
}

async function loadRestaurants() {
  if (S.restaurants) return S.restaurants;
  const res = await api("GET", "/restaurants");
  if (res.status !== 200) throw new Error("restaurants");
  S.restaurants = res.data.restaurants;
  return S.restaurants;
}
async function getRestaurant(id) {
  if (S.details[id]) return S.details[id];
  const res = await api("GET", "/restaurants/" + encodeURIComponent(id));
  if (res.status !== 200) throw new Error("restaurant");
  S.details[id] = res.data;
  return res.data;
}
const labelsOf = (r, ids) => ids.map((id) => (r.tables.find((t) => t.id === id) || { label: id }).label);
const tableIdsOf = (res) => res.table_ids || (res.table_id ? [res.table_id] : []);

function prettyDate(date) {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  });
}
function todayLocal() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// ---------- shell ----------
function navigate(path) {
  if (location.pathname !== path) history.pushState({}, "", path);
  render();
}

function navLink(path, text) {
  const a = h("a", { href: path, "data-link": "" }, text);
  if (location.pathname === path) a.setAttribute("aria-current", "page");
  return a;
}

function shell(content) {
  const right = S.token
    ? h("span", { class: "who" },
        h("span", { testid: "current-user" }, "Signed in as ", h("strong", {}, S.name || "guest")),
        h("button", { class: "btn secondary", type: "button", testid: "logout-button", onclick: logout }, "Sign out"))
    : [navLink("/login", "Sign in"), navLink("/signup", "Sign up")];
  return h("div", {},
    h("header", { class: "topbar" },
      h("div", { class: "topbar-inner" },
        h("a", { class: "brand", href: "/", "data-link": "" }, "Tablekeeper"),
        h("nav", { class: "nav", "aria-label": "Main" },
          navLink("/", "Find a table"), navLink("/lookup", "Find my booking"), right))),
    h("main", {}, content));
}

function logout() {
  setSession(null, null);
  S.booking = null;
  render();
}

document.addEventListener("click", (ev) => {
  const a = ev.target.closest && ev.target.closest("a[data-link]");
  if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
  ev.preventDefault();
  navigate(a.getAttribute("href"));
});
window.addEventListener("popstate", render);

function render() {
  S.seq++;               // anything in flight for the previous screen is stale
  const path = location.pathname;
  const app = document.getElementById("app");
  let content;
  if (path === "/signup") content = authScreen("signup");
  else if (path === "/login") content = authScreen("login");
  else if (path === "/lookup") content = lookupScreen();
  else content = searchScreen();
  app.replaceChildren(shell(content));
  if (path === "/" || path === "") afterSearchMount();
}

// ---------- auth ----------
function authScreen(kind) {
  const signup = kind === "signup";
  const errBox = h("div", {});
  const email = h("input", { id: kind + "-email", type: "email", autocomplete: "email", testid: kind + "-email", required: true });
  const password = h("input", { id: kind + "-password", type: "password", autocomplete: signup ? "new-password" : "current-password", testid: kind + "-password", required: true });
  const name = signup ? h("input", { id: "signup-display-name", type: "text", autocomplete: "name", testid: "signup-display-name", required: true }) : null;
  const submit = h("button", { class: "btn", type: "submit", testid: kind + "-submit" }, signup ? "Create account" : "Sign in");

  const form = h("form", { class: "form-col", novalidate: true },
    h("div", { class: "field" }, h("label", { for: kind + "-email" }, "Email"), email),
    signup ? h("div", { class: "field" }, h("label", { for: "signup-display-name" }, "Your name"), name) : null,
    h("div", { class: "field" }, h("label", { for: kind + "-password" }, signup ? "Password (8+ characters)" : "Password"), password),
    errBox,
    submit);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    errBox.replaceChildren();
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    const label = submit.textContent;
    submit.textContent = signup ? "Creating account…" : "Signing in…";
    try {
      const body = signup
        ? { email: email.value, password: password.value, display_name: name.value }
        : { email: email.value, password: password.value };
      const res = await api("POST", signup ? "/auth/signup" : "/auth/login", body);
      if (res.status === 200 || res.status === 201) {
        setSession(res.data.token, res.data.display_name);
        navigate("/");
        return;
      }
      let text = humanError(res, "Could not complete that request.");
      if (res.status === 401) text = "That email and password do not match.";
      if (res.status === 422 && signup) text = "Please enter a valid email, a name, and a password of at least 8 characters.";
      if (res.status === 422 && !signup) text = "Please enter your email and password.";
      errBox.replaceChildren(msg("error", "auth-error", text));
    } catch (e) {
      errBox.replaceChildren(msg("error", "auth-error", "Network problem. Please try again."));
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.textContent = label;
    }
  });

  return h("div", { class: "stack" },
    h("div", {}, h("h1", {}, signup ? "Create your account" : "Welcome back"),
      h("p", { class: "lede" }, signup ? "Book tables and manage your reservations." : "Sign in to book and manage tables.")),
    h("div", { class: "card narrow" }, form,
      h("p", { class: "lede" }, signup ? "Already registered? " : "New here? ",
        h("a", { href: signup ? "/login" : "/signup", "data-link": "" }, signup ? "Sign in" : "Create an account"))));
}

// ---------- search ----------
function searchScreen() {
  const ui = {};
  S.pageUi = ui;
  ui.select = h("select", { id: "restaurant", testid: "restaurant-select" });
  ui.date = h("input", { id: "date", type: "date", testid: "date-input", value: (S.last && S.last.date) || todayLocal() });
  ui.party = h("input", { id: "party", type: "number", min: "1", step: "1", inputmode: "numeric", testid: "party-size-input", value: (S.last && S.last.party) || "2" });
  ui.button = h("button", { class: "btn", type: "submit", testid: "search-button" }, "Find a table");
  ui.notice = h("div", {});
  ui.results = h("div", {});
  ui.booking = h("div", {});
  const form = h("form", { class: "form-grid search", novalidate: true },
    h("div", { class: "field" }, h("label", { for: "restaurant" }, "Restaurant"), ui.select),
    h("div", { class: "field" }, h("label", { for: "date" }, "Date"), ui.date),
    h("div", { class: "field" }, h("label", { for: "party" }, "Party size"), ui.party),
    ui.button);
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    startSearch({ rid: ui.select.value, date: ui.date.value, party: ui.party.value.trim() });
  });
  ui.results.replaceChildren(emptyState());
  return h("div", { class: "stack" },
    h("div", {}, h("h1", {}, "Find a table"),
      h("p", { class: "lede" }, "Choose a restaurant, a date and your party size.")),
    h("div", { class: "card" }, form),
    ui.notice, ui.booking, ui.results);
}

function emptyState() {
  return h("div", { class: "empty" }, h("strong", {}, "Pick a date and party size to see open tables"),
    "Then choose a time and table to book.");
}

async function afterSearchMount() {
  const ui = S.pageUi;
  try {
    const list = await loadRestaurants();
    if (S.pageUi !== ui) return;
    ui.select.replaceChildren(...list.map((r) => h("option", { value: r.id }, r.name)));
    if (S.last) ui.select.value = S.last.rid;
  } catch (e) {
    if (S.pageUi === ui) ui.results.replaceChildren(msg("error", null, "We could not load the restaurants. Please reload the page."));
  }
  if (S.booking && S.pageUi === ui) buildBookingForm();
}

function startSearch(p) {
  const ui = S.pageUi;
  if (!p.rid) { ui.results.replaceChildren(msg("error", null, "Choose a restaurant first.")); return; }
  if (!/^\d{4}-\d\d-\d\d$/.test(p.date)) { ui.results.replaceChildren(msg("error", null, "Choose a date.")); return; }
  if (!/^\d+$/.test(p.party) || Number(p.party) < 1) { ui.results.replaceChildren(msg("error", null, "Party size must be a whole number of at least 1.")); return; }
  ui.notice.replaceChildren();
  runSearch(p, true);
}

async function runSearch(p, showLoading) {
  const ui = S.pageUi;
  const my = ++S.seq;
  S.last = p;
  if (showLoading) {
    ui.button.setAttribute("aria-busy", "true");
    ui.results.replaceChildren(h("div", { class: "loading", role: "status" }, h("span", { class: "spinner" }), "Checking availability…"));
  }
  try {
    const [rest, av] = await Promise.all([
      getRestaurant(p.rid),
      api("GET", `/availability?restaurant_id=${encodeURIComponent(p.rid)}&date=${encodeURIComponent(p.date)}&party_size=${encodeURIComponent(p.party)}`),
    ]);
    if (my !== S.seq) return;
    if (av.status !== 200) {
      ui.results.replaceChildren(msg("error", null, humanError(av, "We could not load availability.")));
      return;
    }
    S.shown = p;
    renderGrid(ui, rest, av.data, p);
  } catch (e) {
    if (my !== S.seq) return;
    ui.results.replaceChildren(msg("error", null, "We could not load availability. Check your connection and try again."));
  } finally {
    if (my === S.seq) ui.button.removeAttribute("aria-busy");
  }
}

function renderGrid(ui, rest, data, p) {
  const party = Number(p.party);
  const slots = data.slots || [];
  const head = h("div", { class: "results-head" },
    h("h2", {}, `${rest.name} · ${prettyDate(p.date)}`),
    h("ul", { class: "legend", "aria-label": "Legend" },
      h("li", {}, h("span", { class: "swatch" }), "Available"),
      h("li", {}, h("span", { class: "swatch off" }), "Booked"),
      h("li", {}, h("span", { class: "swatch sel" }), "Selected")));
  if (!slots.length) {
    ui.results.replaceChildren(head,
      h("div", { class: "empty", testid: "no-slots" }, h("strong", {}, "No tables on this day"),
        `${rest.name} is closed on ${prettyDate(p.date)}. Try another date.`));
    return;
  }
  const singles = rest.tables.map((t) => ({ key: t.id, ids: [t.id], label: t.label, cap: t.capacity }));
  const capOf = (id) => (rest.tables.find((t) => t.id === id) || { capacity: 0 }).capacity;
  const pairs = (rest.combinable || [])
    .map((pr) => ({ key: pr.join("+"), ids: pr, label: labelsOf(rest, pr).join(" + "), cap: pr.reduce((s, id) => s + capOf(id), 0) }))
    .filter((o) => o.cap >= party);
  const cols = singles.concat(pairs);

  const thead = h("thead", {}, h("tr", {},
    h("th", { scope: "col", class: "time" }, "Time"),
    cols.map((c) => h("th", { scope: "col" }, c.label, h("small", {}, `${c.cap} seats`)))));
  const tbody = h("tbody", {});
  for (const s of slots) {
    const hh = s.starts_at_local.slice(11);
    const freeSingles = new Set(s.available_table_ids || []);
    const freePairs = new Set((s.available_options || []).filter((o) => o.table_ids.length === 2).map((o) => o.table_ids.join("+")));
    const tr = h("tr", {}, h("th", { scope: "row", class: "time" }, hh));
    for (const c of cols) {
      const avail = c.ids.length === 1 ? freeSingles.has(c.key) : freePairs.has(c.key);
      const word = avail ? "Book" : c.cap < party ? "Too small" : "Booked";
      const btn = h("button", {
        type: "button", class: "cell", testid: `slot-${c.key}-${hh}`, "data-available": avail ? "true" : "false",
        "data-key": c.key, "data-time": hh,
        "aria-label": `${c.label} at ${hh}: ${avail ? "available" : word.toLowerCase()}`,
        "aria-disabled": avail ? null : "true",
      }, word);
      btn.addEventListener("click", () => {
        if (btn.getAttribute("data-available") !== "true") return;
        pickCell(rest, c, s.starts_at_local);
      });
      tr.append(h("td", {}, btn));
    }
    tbody.append(tr);
  }
  const grid = h("div", { testid: "availability-grid" },
    h("div", { class: "grid-scroll", tabindex: "0", role: "region", "aria-label": "Availability" },
      h("table", { class: "grid" }, thead, tbody)));
  ui.results.replaceChildren(head, grid);
  markSelected();
}

function markSelected() {
  const b = S.booking;
  const ui = S.pageUi;
  if (!ui) return;
  for (const c of ui.results.querySelectorAll(".cell")) {
    const sel = !!b && S.shown && b.rid === S.shown.rid && b.tableKey === c.getAttribute("data-key") &&
      b.local.slice(11) === c.getAttribute("data-time") && b.local.slice(0, 10) === S.shown.date;
    c.classList.toggle("selected", sel);
    if (sel) c.textContent = "Selected";
    else if (c.textContent === "Selected") c.textContent = c.getAttribute("data-available") === "true" ? "Book" : "Booked";
    if (sel) c.setAttribute("aria-pressed", "true"); else c.removeAttribute("aria-pressed");
  }
}

// ---------- booking ----------
function pickCell(rest, opt, local) {
  const ui = S.pageUi;
  if (!S.token) {
    ui.notice.replaceChildren(msg("error", "auth-error", "Please ",
      h("a", { href: "/login", "data-link": "" }, "sign in"), " or ",
      h("a", { href: "/signup", "data-link": "" }, "create an account"), " to book a table."));
    return;
  }
  ui.notice.replaceChildren();
  const b = S.booking;
  if (b && b.rid === rest.id && b.tableKey === opt.key && b.local === local) return;
  S.booking = {
    rid: rest.id, restaurantName: rest.name, tableIds: opt.ids, tableKey: opt.key,
    labels: labelsOf(rest, opt.ids), cap: opt.cap, local, party: S.shown.party,
    key: null, identity: null, busy: false, ui: null,
  };
  buildBookingForm();
  markSelected();
}

function buildBookingForm() {
  const b = S.booking;
  const ui = S.pageUi;
  const bu = {};
  b.ui = bu;
  bu.party = h("input", { id: "booking-party", type: "number", min: "1", step: "1", inputmode: "numeric", testid: "booking-party-size", value: b.party });
  bu.submit = h("button", { class: "btn", type: "submit", testid: "booking-submit" }, "Confirm booking");
  bu.status = h("div", { class: "status" });
  const form = h("form", { class: "card booking", testid: "booking-form", novalidate: true },
    h("h2", {}, "Reserve this table"),
    h("p", { class: "summary", testid: "booking-summary" },
      `${b.labels.join(" + ")} · ${b.local.slice(11)}`),
    h("p", { class: "summary-meta" }, `${b.restaurantName} · ${prettyDate(b.local.slice(0, 10))} · seats up to ${b.cap}`),
    h("div", { class: "booking-row" },
      h("div", { class: "field" }, h("label", { for: "booking-party" }, "Party size"), bu.party),
      bu.submit),
    bu.status);
  form.addEventListener("submit", (ev) => { ev.preventDefault(); submitBooking(); });
  ui.booking.replaceChildren(form);
}

function bookingBusy(b, busy) {
  b.busy = busy;
  b.ui.submit.disabled = busy;
  if (busy) b.ui.submit.setAttribute("aria-busy", "true"); else b.ui.submit.removeAttribute("aria-busy");
  b.ui.submit.textContent = busy ? "Booking…" : b.uncertain ? "Retry booking" : "Confirm booking";
}

async function submitBooking() {
  const b = S.booking;
  if (!b || b.busy) return;
  const raw = b.ui.party.value.trim();
  const body = {
    restaurant_id: b.rid, table_ids: b.tableIds, starts_at_local: b.local,
    party_size: raw === "" ? null : Number(raw),
  };
  const identity = JSON.stringify(body);
  if (identity !== b.identity) { b.identity = identity; b.key = uuid(); }
  b.ui.status.replaceChildren();
  bookingBusy(b, true);
  let res;
  try {
    res = await api("POST", "/reservations", body, { "Idempotency-Key": b.key });
  } catch (e) {
    return uncertain(b);
  }
  b.uncertain = false;
  const d = res.data;
  if ((res.status === 200 || res.status === 201) && d && d.reference) {
    bookingBusy(b, false);
    b.ui.status.replaceChildren(confirmation(b, d));
    refreshGrid();
    return;
  }
  if (res.status >= 500 || (res.status < 400)) return uncertain(b);
  bookingBusy(b, false);
  b.ui.status.replaceChildren(msg("error", "booking-error", humanError(res, "We could not complete that booking.")));
  if (res.status === 409) refreshGrid();
}

function uncertain(b) {
  b.uncertain = true;
  bookingBusy(b, false);
  b.ui.status.replaceChildren(msg("warn", "booking-uncertain",
    "We did not hear back from the restaurant, so we cannot tell yet whether this table is booked. ",
    "Press Retry booking to check: it is safe and will never book twice."));
}

function confirmation(b, d) {
  const ids = tableIdsOf(d);
  const names = ids.length ? ids.map((id) => b.labels[b.tableIds.indexOf(id)] || id) : b.labels;
  return h("div", { class: "msg success", testid: "confirmation", role: "status" },
    h("div", { class: "msg-body confirm-grid" },
      h("strong", {}, "Your table is confirmed. Reference:"),
      h("span", { class: "conf-ref", testid: "confirmation-reference" }, d.reference),
      h("span", { testid: "confirmation-details" },
        `${b.restaurantName} · `, h("span", { testid: "confirmation-tables" }, names.join(" + ")),
        ` · ${prettyDate(d.starts_at_local.slice(0, 10))} at ${d.starts_at_local.slice(11)} · party of ${d.party_size}`)));
}

function refreshGrid() {
  if (S.last && S.pageUi && location.pathname === "/") runSearch(S.last, false);
}

// ---------- lookup ----------
function lookupScreen() {
  const out = h("div", {});
  const input = h("input", { id: "ref", type: "text", autocomplete: "off", autocapitalize: "characters", testid: "lookup-reference-input" });
  const submit = h("button", { class: "btn", type: "submit", testid: "lookup-submit" }, "Find booking");
  const form = h("form", { class: "form-grid", novalidate: true, style: "grid-template-columns:1fr" },
    h("div", { class: "field" }, h("label", { for: "ref" }, "Booking reference"), input), submit);
  const my = { n: 0 };
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const n = ++my.n;
    const ref = input.value.trim();
    if (!S.token) {
      out.replaceChildren(msg("error", "reservation-error", "Please ",
        h("a", { href: "/login", "data-link": "" }, "sign in"), " to look up your booking."));
      return;
    }
    if (!ref) { out.replaceChildren(msg("error", "reservation-error", "Enter the reference from your confirmation.")); return; }
    submit.setAttribute("aria-busy", "true");
    out.replaceChildren(h("div", { class: "loading", role: "status" }, h("span", { class: "spinner" }), "Looking up…"));
    try {
      const res = await api("GET", "/reservations/" + encodeURIComponent(ref));
      if (n !== my.n) return;
      if (res.status !== 200) {
        out.replaceChildren(msg("error", "reservation-error",
          res.status === 404 ? "We could not find a booking with that reference under your account." : humanError(res)));
        return;
      }
      const rest = await getRestaurant(res.data.restaurant_id).catch(() => null);
      if (n !== my.n) return;
      showReservation(out, res.data, rest, null);
    } catch (e) {
      if (n === my.n) out.replaceChildren(msg("error", "reservation-error", "Network problem. Please try again."));
    } finally {
      submit.removeAttribute("aria-busy");
    }
  });
  return h("div", { class: "stack" },
    h("div", {}, h("h1", {}, "Find my booking"),
      h("p", { class: "lede" }, "Enter your reference to review or cancel a reservation.")),
    h("div", { class: "card narrow" }, form),
    out);
}

function showReservation(out, r, rest, errorText) {
  const ids = tableIdsOf(r);
  const names = rest ? labelsOf(rest, ids) : ids;
  const cancelBtn = r.status === "confirmed"
    ? h("button", { class: "btn", type: "button", testid: "reservation-cancel-button", onclick: async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true; btn.setAttribute("aria-busy", "true"); btn.textContent = "Cancelling…";
        try {
          const res = await api("POST", `/reservations/${encodeURIComponent(r.reference)}/cancel`);
          if (res.status === 200 && res.data) { showReservation(out, res.data, rest, null); return; }
          showReservation(out, r, rest, res.status === 409 && res.data && res.data.error.code === "cutoff_passed"
            ? "This booking can no longer be cancelled online because it starts too soon. Please call the restaurant."
            : humanError(res, "We could not cancel this booking."));
        } catch (e) {
          showReservation(out, r, rest, "Network problem. We could not confirm the cancellation. Please check again.");
        }
      } }, "Cancel booking")
    : null;
  out.replaceChildren(...[
    errorText ? msg("error", "reservation-error", errorText) : null,
    h("div", { class: "card", testid: "reservation-detail", style: errorText ? "margin-top:16px" : null },
      h("div", { class: "row-actions" }, h("h2", {}, rest ? rest.name : "Your booking"),
        h("span", { class: "badge " + r.status, testid: "reservation-status" }, r.status)),
      h("dl", { class: "facts" },
        h("dt", {}, "Reference"), h("dd", {}, r.reference),
        h("dt", {}, "Table"), h("dd", { testid: "reservation-tables" }, names.join(" + ")),
        h("dt", {}, "When"), h("dd", {}, `${prettyDate(r.starts_at_local.slice(0, 10))} at ${r.starts_at_local.slice(11)}`),
        h("dt", {}, "Party"), h("dd", {}, String(r.party_size))),
      cancelBtn ? h("div", { class: "row-actions" }, cancelBtn) : null)].filter(Boolean));
}

render();
