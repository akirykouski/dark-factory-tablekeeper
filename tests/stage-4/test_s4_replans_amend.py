"""S4-01..S4-25: closure replanning and series amendments."""
import copy
import os
import uuid

import pytest

from conftest import PAST_THU, THU, Api, assert_error, base_fixture, parallel

THU2, THU3, THU4 = "2030-10-03", "2030-10-10", "2030-10-17"
FROM, TO = f"{THU}T18:00:00+02:00", f"{THU}T23:00:00+02:00"


def fx4():
    fx = copy.deepcopy(base_fixture())
    fx["users"].append({"id": "u_mgr", "email": "mgr@example.com", "password": "manager pass",
                        "display_name": "Manager"})
    r = fx["restaurants"][0]
    r["tables"] = [{"id": "t_1", "label": "1", "capacity": 2},
                   {"id": "t_2", "label": "2", "capacity": 4},
                   {"id": "t_3", "label": "3", "capacity": 6},
                   {"id": "t_4", "label": "4", "capacity": 2}]
    r["combinable"] = [["t_1", "t_4"], ["t_1", "t_2"]]
    r["manager_user_ids"] = ["u_mgr"]
    fx["restaurants"][1]["manager_user_ids"] = ["u_mgr"]
    return fx


@pytest.fixture
def a4(api):
    api.reset(fx4())
    return api


@pytest.fixture
def ada(a4):
    return a4.login()


@pytest.fixture
def bob(a4):
    return a4.login("bob@example.com", "battery staple")


@pytest.fixture
def mgr(a4):
    return a4.login("mgr@example.com", "manager pass")


def preview(api, tok, table="t_2", frm=FROM, to=TO, key=None, rid="r_anker"):
    return api.post(f"/restaurants/{rid}/replans", {"table_id": table, "from": frm, "to": to},
                    token=tok, key=key or uuid.uuid4().hex)


def apply(api, tok, plan_id, key=None, rid="r_anker"):
    return api.post(f"/restaurants/{rid}/replans/{plan_id}/apply", {}, token=tok,
                    key=key or uuid.uuid4().hex)


def hist(api, tok, ref):
    return api.get(f"/reservations/{ref}/history", token=tok).json()["entries"]


# --- preview -------------------------------------------------------------------------

def test_preview_shape_and_choice(a4, ada, mgr):
    a = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3).json()
    b = a4.book(ada, table_id="t_3", local=f"{THU}T20:00", party_size=5).json()
    outside = a4.book(ada, table_id="t_2", local=f"{THU2}T19:00").json()
    r = preview(a4, mgr)
    assert r.status_code == 201, r.text
    p = r.json()
    assert set(p) == {"plan_id", "restaurant_revision", "closure", "assignments",
                      "moved_count", "unused_seats"}
    assert p["restaurant_revision"] == 3
    assert p["closure"] == {"table_id": "t_2", "from": FROM, "to": TO}
    exp = sorted([
        {"reference": a["reference"], "table_ids": ["t_1", "t_4"], "changed": True},
        {"reference": b["reference"], "table_ids": ["t_3"], "changed": False}],
        key=lambda x: x["reference"])
    assert p["assignments"] == exp
    assert p["moved_count"] == 1 and p["unused_seats"] == 2
    assert outside["reference"] not in [x["reference"] for x in p["assignments"]]
    # preview changes nothing
    assert a4.get(f"/reservations/{a['reference']}", token=ada).json() == a
    assert len(hist(a4, ada, a["reference"])) == 1
    s = a4.slot("r_anker", THU, 1, f"{THU}T21:00")
    assert "t_2" in s["available_table_ids"]
    p2 = preview(a4, mgr).json()
    assert p2["restaurant_revision"] == 3 and p2["plan_id"] != p["plan_id"]


def test_rank_tiebreak(a4, ada, mgr):
    a = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=2).json()
    p = preview(a4, mgr).json()
    assert p["assignments"] == [{"reference": a["reference"], "table_ids": ["t_1"],
                                 "changed": True}]
    assert p["unused_seats"] == 0


def test_minimise_moves_first(a4, ada, mgr):
    x1 = a4.book(ada, table_id="t_1", local=f"{THU}T19:00", party_size=2).json()
    x4 = a4.book(ada, table_id="t_4", local=f"{THU}T19:00", party_size=2).json()
    y = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=2).json()
    p = preview(a4, mgr).json()
    got = {x["reference"]: (x["table_ids"], x["changed"]) for x in p["assignments"]}
    assert got[y["reference"]] == (["t_3"], True)
    assert got[x1["reference"]] == (["t_1"], False)
    assert got[x4["reference"]] == (["t_4"], False)
    assert p["moved_count"] == 1 and p["unused_seats"] == 4


def test_uses_own_accepted_capacities(a4, ada, mgr):
    a = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3).json()
    # a later policy enlarges t_1 to 4; the booking keeps policy-0 capacities (t_1 = 2)
    a4.post("/restaurants/r_anker/policies", {
        "effective_from": "2030-01-01", "slot_minutes": 30, "reservation_duration_minutes": 90,
        "cancellation_cutoff_minutes": 120,
        "opening_hours": [{"weekday": "thu", "opens": "18:00", "closes": "23:00"}],
        "capacities": {"t_1": 4, "t_2": 4, "t_3": 6, "t_4": 2}}, token=mgr, key="pol")
    p = preview(a4, mgr).json()
    assert p["assignments"][0]["table_ids"] == ["t_1", "t_4"]


def test_no_feasible_plan(a4, ada, bob, mgr):
    a = a4.book(ada, table_id="t_3", local=f"{THU}T19:00", party_size=6).json()
    a4.book(bob, table_id="t_2", local=f"{PAST_THU}T19:00")  # elsewhere in time
    fixed = a4.book(bob, table_id="t_1", local=f"{THU2}T19:00")
    # t_1+t_2 is the only other option with 6 seats; occupy t_2 in the window
    a4.book(bob, table_id="t_2", local=f"{THU}T19:30", party_size=4)
    r = preview(a4, mgr, table="t_3", key="nf")
    # bob's t_2 booking is itself considered and could move... to t_3? closed. to t_1+t_4? 4 seats
    # only if t_1 and t_4 are free -- then a goes to t_1+t_2? t_1 is used by bob's pair. So
    # infeasible.
    assert_error(r, 409, "no_feasible_plan")
    assert a4.get(f"/reservations/{a['reference']}", token=ada).json() == a
    # key not consumed
    r2 = preview(a4, mgr, table="t_1", key="nf")
    assert r2.status_code == 201


def test_planning_limit(a4, ada, mgr):
    for t in ["t_1", "t_2", "t_3", "t_4"]:
        a4.book(ada, table_id=t, local=f"{THU}T18:00")
    for t in ["t_1", "t_2", "t_3"]:
        a4.book(ada, table_id=t, local=f"{THU}T20:00")
    assert_error(preview(a4, mgr), 422, "planning_limit")


@pytest.mark.parametrize("body", [
    {"table_id": "t_2", "from": FROM},
    {"table_id": "t_2", "to": TO},
    {"from": FROM, "to": TO},
    {"table_id": 2, "from": FROM, "to": TO},
    {"table_id": "t_2", "from": TO, "to": FROM},
    {"table_id": "t_2", "from": FROM, "to": FROM},
    {"table_id": "t_2", "from": f"{THU}T18:00:00", "to": TO},
    {"table_id": "t_2", "from": f"{THU}", "to": TO},
    {"table_id": "t_2", "from": "yesterday", "to": TO},
    {"table_id": "t_2", "from": 5, "to": TO},
    {"table_id": "t_2", "from": FROM, "to": None},
])
def test_preview_validation(a4, mgr, body):
    r = a4.post("/restaurants/r_anker/replans", body, token=mgr, key=uuid.uuid4().hex)
    assert_error(r, 422, "validation_failed")


def test_preview_access(a4, ada, mgr):
    assert_error(a4.post("/restaurants/r_anker/replans",
                         {"table_id": "t_2", "from": FROM, "to": TO}, key="k"), 401,
                 "unauthenticated")
    assert_error(preview(a4, ada), 403, "forbidden")
    assert_error(preview(a4, mgr, rid="nope"), 404, "not_found")
    assert_error(preview(a4, mgr, table="nope"), 404, "not_found")
    assert_error(preview(a4, mgr, table="h_1"), 404, "not_found")
    assert_error(a4.post("/restaurants/r_anker/replans",
                         {"table_id": "t_2", "from": FROM, "to": TO}, token=mgr), 400,
                 "missing_idempotency_key")


def test_preview_accepts_z(a4, mgr):
    r = preview(a4, mgr, frm=f"{THU}T16:00:00Z", to=f"{THU}T21:00:00Z")
    assert r.status_code == 201
    assert r.json()["assignments"] == [] and r.json()["moved_count"] == 0


def test_preview_replay(a4, ada, mgr):
    a4.book(ada, table_id="t_2", local=f"{THU}T19:00")
    r1 = preview(a4, mgr, key="pv")
    r2 = preview(a4, mgr, key="pv")
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert_error(preview(a4, mgr, table="t_1", key="pv"), 409, "idempotency_key_reuse")


# --- apply ----------------------------------------------------------------------------

def test_apply(a4, ada, mgr):
    a = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3).json()
    b = a4.book(ada, table_id="t_3", local=f"{THU}T20:00", party_size=5).json()
    p = preview(a4, mgr).json()
    r = apply(a4, mgr, p["plan_id"], key="ap")
    assert r.status_code == 201, r.text
    out = r.json()
    assert set(out) == {"plan_id", "restaurant_revision", "reservations"}
    assert out["plan_id"] == p["plan_id"] and out["restaurant_revision"] == 3
    refs = sorted([a["reference"], b["reference"]])
    assert [x["reference"] for x in out["reservations"]] == refs
    na = a4.get(f"/reservations/{a['reference']}", token=ada).json()
    assert na["table_ids"] == ["t_1", "t_4"] and "table_id" not in na
    assert na["revision"] == 2
    for k in ["starts_at", "ends_at", "starts_at_local", "party_size", "accepted_terms",
              "created_at", "reservation_id", "status"]:
        assert na[k] == a[k], k
    assert a4.get(f"/reservations/{b['reference']}", token=ada).json() == b
    es = hist(a4, ada, a["reference"])
    assert es[-1]["event"] == "reassigned"
    assert es[-1]["plan_id"] == p["plan_id"]
    assert es[-1]["changes"] == [{"field": "table_ids", "from": ["t_2"], "to": ["t_1", "t_4"]}]
    assert es[-1]["revision"] == 2 and es[-1]["accepted_terms"] == a["accepted_terms"]
    assert len(hist(a4, ada, b["reference"])) == 1
    # closure in force
    s = a4.slot("r_anker", THU, 1, f"{THU}T18:00")
    assert "t_2" not in s["available_table_ids"]
    assert all("t_2" not in o["table_ids"] for o in s["available_options"])
    ex = a4.avail(party_size=1, explain="true").json()["slots"][0]["explain"]
    t2 = [e for e in ex if e["table_id"] == "t_2"][0]
    assert t2["available"] is False and t2["rules"][1] == {"rule": "no_overlap", "holds": False}
    assert_error(a4.book(ada, table_id="t_2", local=f"{THU}T21:30", expect=None), 409,
                 "table_unavailable")
    c = a4.book(ada, table_id="t_2", local=f"{THU2}T19:00").json()
    assert_error(a4.patch(f"/reservations/{c['reference']}", {"starts_at_local": f"{THU}T21:00"},
                          token=ada), 409, "table_unavailable")
    # replay
    r2 = apply(a4, mgr, p["plan_id"], key="ap")
    assert r2.status_code == 200 and r2.json() == out
    assert_error(apply(a4, mgr, p["plan_id"], key="other"), 409, "plan_already_applied")


def test_stale_plan(a4, ada, mgr):
    a4.book(ada, table_id="t_2", local=f"{THU}T19:00")
    p = preview(a4, mgr).json()
    a4.book(ada, table_id="t_1", local=f"{THU2}T19:00")     # intervening write
    assert_error(apply(a4, mgr, p["plan_id"]), 409, "stale_plan")
    p2 = preview(a4, mgr).json()
    assert p2["restaurant_revision"] == p["restaurant_revision"] + 1
    assert apply(a4, mgr, p2["plan_id"]).status_code == 201


def test_noop_and_failures_do_not_stale(a4, ada, mgr):
    b = a4.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    p = preview(a4, mgr).json()
    a4.patch(f"/reservations/{b['reference']}", {"party_size": b["party_size"]}, token=ada)
    a4.book(ada, table_id="t_2", local=f"{THU}T19:00", expect=409)
    a4.post("/restaurants/r_anker/policies", {"effective_from": "x"}, token=mgr, key="bad")
    preview(a4, mgr)
    # other restaurant activity
    a4.book(ada, restaurant_id="r_hudson", table_id="h_1", local=f"{THU}T19:00")
    assert apply(a4, mgr, p["plan_id"]).status_code == 201


@pytest.mark.parametrize("write", ["book", "cancel", "amend", "policy"])
def test_each_write_kind_stales(a4, ada, mgr, write):
    b = a4.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    other = a4.book(ada, table_id="t_1", local=f"{THU2}T19:00").json()
    p = preview(a4, mgr).json()
    if write == "book":
        a4.book(ada, table_id="t_3", local=f"{THU2}T19:00")
    elif write == "cancel":
        a4.post(f"/reservations/{other['reference']}/cancel", {}, token=ada)
    elif write == "amend":
        a4.patch(f"/reservations/{other['reference']}", {"party_size": 1}, token=ada)
    else:
        a4.post("/restaurants/r_anker/policies", {
            "effective_from": "2031-01-01", "slot_minutes": 30,
            "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
            "opening_hours": [], "capacities": {"t_1": 2, "t_2": 4, "t_3": 6, "t_4": 2}},
            token=mgr, key="pp")
    assert_error(apply(a4, mgr, p["plan_id"]), 409, "stale_plan")


def test_apply_access(a4, ada, mgr):
    a4.book(ada, table_id="t_2", local=f"{THU}T19:00")
    p = preview(a4, mgr).json()
    assert_error(apply(a4, ada, p["plan_id"]), 403, "forbidden")
    assert_error(a4.post(f"/restaurants/r_anker/replans/{p['plan_id']}/apply", {}, key="z"),
                 401, "unauthenticated")
    assert_error(apply(a4, mgr, "nope"), 404, "not_found")
    assert_error(apply(a4, mgr, p["plan_id"], rid="r_hudson"), 404, "not_found")
    assert_error(a4.post(f"/restaurants/r_anker/replans/{p['plan_id']}/apply", {}, token=mgr),
                 400, "missing_idempotency_key")


def test_previous_closure_respected(a4, ada, mgr):
    p = preview(a4, mgr, table="t_2").json()
    apply(a4, mgr, p["plan_id"])
    a = a4.book(ada, table_id="t_1", local=f"{THU}T19:00", party_size=2).json()
    # closing t_1 too: t_2 (closed), pairs need t_1 -> t_3 or t_4
    p2 = preview(a4, mgr, table="t_1").json()
    assert p2["assignments"] == [{"reference": a["reference"], "table_ids": ["t_4"],
                                  "changed": True}]


def test_concurrent_apply(a4, ada, mgr):
    a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3)
    a4.book(ada, table_id="t_1", local=f"{THU}T20:00", party_size=2)
    p = preview(a4, mgr).json()
    rs = parallel([lambda k=k: apply(a4, mgr, p["plan_id"], key=f"c{k}") for k in range(20)])
    codes = [r.status_code for r in rs]
    assert codes.count(201) == 1, codes
    assert all(c in (201, 409) for c in codes)


def test_series_members_moved_by_plan(a4, ada, mgr):
    a = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=2).json()
    s = a4.post("/series", {"anchor_reference": a["reference"], "count": 3,
                            "interval_weeks": 1}, token=ada, key="s").json()
    o1 = s["occurrences"][1]["reference"]
    a4.patch(f"/reservations/{o1}", {"party_size": 1}, token=ada)  # exception
    p = preview(a4, mgr, frm=f"{THU}T18:00:00+02:00", to=f"{THU2}T23:00:00+02:00").json()
    assert p["moved_count"] == 2
    apply(a4, mgr, p["plan_id"])
    g = a4.get(f"/series/{s['series_id']}", token=ada).json()
    assert g["revision"] == 3   # 1 + exception patch + one plan application
    assert [o["exception"] for o in g["occurrences"]] == [False, True, False]
    assert [o["reservation"]["starts_at_local"] for o in g["occurrences"]] == [
        f"{THU}T19:00", f"{THU2}T19:00", f"{THU3}T19:00"]
    assert g["occurrences"][0]["reservation"]["table_ids"] == ["t_1"]


# --- series amend ------------------------------------------------------------------------

@pytest.fixture
def ser(a4, ada):
    a = a4.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=2).json()
    s = a4.post("/series", {"anchor_reference": a["reference"], "count": 4,
                            "interval_weeks": 1}, token=ada, key="ser").json()
    return s


def amend(api, tok, sid, body, key=None):
    return api.post(f"/series/{sid}/amend", body, token=tok, key=key or uuid.uuid4().hex)


def test_amend_basic(a4, ada, mgr, ser):
    sid = ser["series_id"]
    rev0 = preview(a4, mgr, table="t_3").json()["restaurant_revision"]
    r = amend(a4, ada, sid, {"expected_revision": 1, "from_index": 1, "local_time": "20:00",
                             "x": 1}, key="am")
    assert r.status_code == 201, r.text
    s = r.json()
    assert set(s) == {"series_id", "revision", "interval_weeks", "occurrences"}
    assert s["revision"] == 2
    assert [o["reservation"]["starts_at_local"] for o in s["occurrences"]] == [
        f"{THU}T19:00", f"{THU2}T20:00", f"{THU3}T20:00", f"{THU4}T20:00"]
    assert [o["exception"] for o in s["occurrences"]] == [False] * 4
    assert [o["reservation"]["revision"] for o in s["occurrences"]] == [1, 2, 2, 2]
    es = hist(a4, ada, s["occurrences"][1]["reference"])
    assert es[-1]["event"] == "changed"
    assert es[-1]["changes"] == [{"field": "starts_at_local", "from": f"{THU2}T19:00",
                                  "to": f"{THU2}T20:00"}]
    assert preview(a4, mgr, table="t_3").json()["restaurant_revision"] == rev0 + 1
    assert a4.get(f"/series/{sid}", token=ada).json() == s
    r2 = amend(a4, ada, sid, {"expected_revision": 1, "from_index": 1, "local_time": "20:00",
                              "x": 1}, key="am")
    assert r2.status_code == 200 and r2.json() == s


def test_amend_skips_exceptions_and_cancelled(a4, ada, ser):
    sid = ser["series_id"]
    o = [x["reference"] for x in ser["occurrences"]]
    a4.patch(f"/reservations/{o[2]}", {"party_size": 1}, token=ada)       # exception, rev 2
    a4.post(f"/reservations/{o[3]}/cancel", {}, token=ada)              # rev 3
    s = amend(a4, ada, sid, {"expected_revision": 3, "from_index": 0,
                             "local_time": "18:30"}).json()
    assert [x["reservation"]["starts_at_local"] for x in s["occurrences"]] == [
        f"{THU}T18:30", f"{THU2}T18:30", f"{THU3}T19:00", f"{THU4}T19:00"]
    assert s["revision"] == 4


def test_amend_noop(a4, ada, ser):
    sid = ser["series_id"]
    r = amend(a4, ada, sid, {"expected_revision": 1, "from_index": 0, "local_time": "19:00"})
    assert r.status_code == 201
    assert r.json()["revision"] == 1
    assert [o["reservation"]["revision"] for o in r.json()["occurrences"]] == [1, 1, 1, 1]
    r = amend(a4, ada, sid, {"expected_revision": 1, "from_index": 3, "local_time": "19:00"})
    assert r.json()["revision"] == 1


def test_amend_stale_first(a4, ada, ser):
    sid = ser["series_id"]
    assert_error(amend(a4, ada, sid, {"expected_revision": 2, "from_index": 0,
                                      "local_time": "05:00"}), 409, "stale_revision")


BAD = [{"expected_revision": 0}, {"expected_revision": True}, {"expected_revision": "1"},
       {"expected_revision": None}, {"from_index": -1}, {"from_index": 4},
       {"from_index": True}, {"from_index": 1.0}, {"local_time": "24:00"},
       {"local_time": "7:00"}, {"local_time": "19:00:00"}, {"local_time": " 19:00"},
       {"local_time": "19:60"}, {"local_time": 1900}, {"local_time": None}]


@pytest.mark.parametrize("bad", BAD, ids=[repr(b) for b in BAD])
def test_amend_validation(a4, ada, ser, bad):
    body = {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}
    body.update(bad)
    assert_error(amend(a4, ada, ser["series_id"], body), 422, "validation_failed")


def test_amend_access(a4, ada, bob, ser):
    body = {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}
    assert_error(amend(a4, bob, ser["series_id"], body), 404, "not_found")
    assert_error(amend(a4, ada, "nope", body), 404, "not_found")
    assert_error(a4.post(f"/series/{ser['series_id']}/amend", body, key="k"), 401,
                 "unauthenticated")
    assert_error(a4.post(f"/series/{ser['series_id']}/amend", body, token=ada), 400,
                 "missing_idempotency_key")


def test_amend_conflict_atomic(a4, ada, bob, ser):
    sid = ser["series_id"]
    a4.book(bob, table_id="t_2", local=f"{THU3}T21:00")
    before = a4.get(f"/series/{sid}", token=ada).json()
    assert_error(amend(a4, ada, sid, {"expected_revision": 1, "from_index": 0,
                                      "local_time": "20:00"}, key="cf"), 409,
                 "table_unavailable")
    assert a4.get(f"/series/{sid}", token=ada).json() == before
    assert len(hist(a4, ada, ser["occurrences"][1]["reference"])) == 1


def test_amend_validation_errors_in_index_order(a4, ada, ser):
    sid = ser["series_id"]
    # 22:00 + 90 > 23:00 closes -> outside_opening_hours on every occurrence
    assert_error(amend(a4, ada, sid, {"expected_revision": 1, "from_index": 0,
                                      "local_time": "22:00"}), 422, "outside_opening_hours")
    assert_error(amend(a4, ada, sid, {"expected_revision": 1, "from_index": 0,
                                      "local_time": "19:15"}), 422, "not_on_slot_grid")


def test_amend_within_series_slots_do_not_self_conflict(a4, ada, ser):
    sid = ser["series_id"]
    r = amend(a4, ada, sid, {"expected_revision": 1, "from_index": 0, "local_time": "19:30"})
    assert r.status_code == 201


def test_amend_concurrent(a4, ada, ser):
    sid = ser["series_id"]
    rs = parallel([lambda t=t: amend(a4, ada, sid, {"expected_revision": 1, "from_index": 1,
                                                    "local_time": t})
                   for t in ["18:00", "18:30", "20:00", "20:30", "21:00"] * 4])
    ok = [r for r in rs if r.status_code == 201]
    assert len(ok) == 1, [r.status_code for r in rs]
    assert a4.get(f"/series/{sid}", token=ada).json()["revision"] == 2


def test_export_import_stage4(a4, ada, mgr, ser):
    a4.book(ada, table_id="t_3", local=f"{THU}T19:00")
    p = preview(a4, mgr, table="t_3").json()
    ap = apply(a4, mgr, p["plan_id"], key="ap4")
    snap = a4.export()
    a4.reset()
    assert a4.import_(snap).status_code == 204
    r = apply(a4, mgr, p["plan_id"], key="ap4")
    assert r.status_code == 200 and r.json() == ap.json()
    assert_error(a4.book(ada, table_id="t_3", local=f"{THU}T21:30", expect=None), 409,
                 "table_unavailable")
    p2 = preview(a4, mgr, table="t_1").json()
    assert p2["restaurant_revision"] == ap.json()["restaurant_revision"]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_upgrade_then_replan(api, n):
    url = os.environ.get(f"TK_PREV_URL_{n}")
    if not url:
        pytest.skip(f"TK_PREV_URL_{n} not set")
    old = Api(url)
    fx = fx4()
    if n < 2:
        fx["restaurants"][0].pop("combinable")
    if n < 3:
        for r in fx["restaurants"]:
            r.pop("manager_user_ids")
    old.reset(fx)
    ada = old.login()
    a = old.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=2).json()
    c = old.book(ada, table_id="t_3", local=f"{THU}T21:00").json()
    old.post(f"/reservations/{c['reference']}/cancel", {}, token=ada)
    sid = None
    if n >= 3:
        s = old.post("/series", {"anchor_reference": a["reference"], "count": 3,
                                 "interval_weeks": 1}, token=ada, key="os").json()
        sid = s["series_id"]
        old.post(f"/reservations/{s['occurrences'][2]['reference']}/cancel", {}, token=ada)
    snap = old.export()
    old.client.close()
    assert api.import_(snap).status_code == 204
    # managers are not part of earlier fixtures: re-seeding is not allowed, so only stage-3
    # data carries a manager; still, history and retries must hold
    assert api.get(f"/reservations/{a['reference']}/history", token=ada).status_code == 200
    if n >= 3:
        mgr = api.login("mgr@example.com", "manager pass")
        p = preview(api, mgr).json()
        assert p["moved_count"] == 1
        assert apply(api, mgr, p["plan_id"]).status_code == 201
        r = amend(api, ada, sid, {"expected_revision": 3, "from_index": 1,
                                  "local_time": "20:00"})
        assert r.status_code == 201, r.text
        occ = r.json()["occurrences"]
        assert occ[1]["reservation"]["starts_at_local"] == f"{THU2}T20:00"
        assert occ[2]["reservation"]["status"] == "cancelled"
