"""S3-34..S3-46: recurring reservations, and upgrades from earlier builds."""
import copy
import os
import uuid

import pytest

from conftest import PAST_THU, RES_KEYS, THU, Api, assert_error, base_fixture, parallel

THU2, THU3, THU4 = "2030-10-03", "2030-10-10", "2030-10-17"


def fx3():
    fx = copy.deepcopy(base_fixture())
    fx["users"].append({"id": "u_mgr", "email": "mgr@example.com", "password": "manager pass",
                        "display_name": "Manager"})
    r = fx["restaurants"][0]
    r["combinable"] = [["t_1", "t_2"]]
    r["manager_user_ids"] = ["u_mgr"]
    return fx


@pytest.fixture
def a3(api):
    api.reset(fx3())
    return api


@pytest.fixture
def ada(a3):
    return a3.login()


@pytest.fixture
def bob(a3):
    return a3.login("bob@example.com", "battery staple")


@pytest.fixture
def mgr(a3):
    return a3.login("mgr@example.com", "manager pass")


def series(api, tok, anchor, count=4, interval=1, key=None, **extra):
    body = {"anchor_reference": anchor, "count": count, "interval_weeks": interval}
    body.update(extra)
    return api.post("/series", body, token=tok, key=key or uuid.uuid4().hex)


def test_create_series(a3, ada):
    anchor = a3.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3).json()
    h0 = a3.get(f"/reservations/{anchor['reference']}/history", token=ada).json()
    r = series(a3, ada, anchor["reference"], count=4, interval=1, extra_field=1)
    assert r.status_code == 201, r.text
    s = r.json()
    assert set(s) == {"series_id", "revision", "interval_weeks", "occurrences"}
    assert s["revision"] == 1 and s["interval_weeks"] == 1
    occ = s["occurrences"]
    assert [o["index"] for o in occ] == [0, 1, 2, 3]
    for o in occ:
        assert set(o) == {"index", "reference", "exception", "reservation"}
        assert o["exception"] is False
        assert o["reservation"]["reference"] == o["reference"]
        assert set(o["reservation"]) == RES_KEYS
    assert occ[0]["reference"] == anchor["reference"]
    assert occ[0]["reservation"] == anchor
    assert len({o["reference"] for o in occ}) == 4
    assert [o["reservation"]["starts_at_local"] for o in occ] == [
        f"{THU}T19:00", f"{THU2}T19:00", f"{THU3}T19:00", f"{THU4}T19:00"]
    for o in occ[1:]:
        res = o["reservation"]
        assert res["table_id"] == "t_2" and res["party_size"] == 3
        assert res["revision"] == 1 and res["status"] == "confirmed"
        h = a3.get(f"/reservations/{o['reference']}/history", token=ada).json()
        assert [e["event"] for e in h["entries"]] == ["created"]
    assert a3.get(f"/reservations/{anchor['reference']}/history", token=ada).json() == h0
    lst = a3.get("/reservations", token=ada).json()["reservations"]
    assert len(lst) == 4
    assert "t_2" not in a3.slot("r_anker", THU3, 1, f"{THU3}T19:00")["available_table_ids"]
    g = a3.get(f"/series/{s['series_id']}", token=ada)
    assert g.status_code == 200 and g.json() == s


def test_interval_and_per_date_policy(a3, ada, mgr):
    anchor = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    p = {"effective_from": THU3, "slot_minutes": 15, "reservation_duration_minutes": 120,
         "cancellation_cutoff_minutes": 60,
         "opening_hours": [{"weekday": "thu", "opens": "17:00", "closes": "23:00"}],
         "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}
    assert a3.post("/restaurants/r_anker/policies", p, token=mgr, key="p").status_code == 201
    s = series(a3, ada, anchor["reference"], count=3, interval=1).json()
    occ = s["occurrences"]
    assert occ[1]["reservation"]["accepted_terms"]["policy_version"] == 0
    assert occ[2]["reservation"]["accepted_terms"]["policy_version"] == 1
    assert occ[2]["reservation"]["ends_at"] == f"{THU3}T21:00:00+02:00"
    anchor2 = a3.book(ada, table_id="t_1", local=f"{THU}T18:00").json()
    s2 = series(a3, ada, anchor2["reference"], count=2, interval=4).json()
    assert s2["occurrences"][1]["reservation"]["starts_at_local"] == "2030-10-24T18:00"
    assert s2["interval_weeks"] == 4


def test_occurrence_conflict_rolls_back(a3, ada, bob):
    anchor = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    blocker = a3.book(bob, table_id="t_2", local=f"{THU3}T20:00").json()
    before = a3.get("/reservations", token=ada).json()
    assert_error(series(a3, ada, anchor["reference"], key="sk"), 409, "table_unavailable")
    assert a3.get("/reservations", token=ada).json() == before
    assert "t_2" in a3.slot("r_anker", THU2, 1, f"{THU2}T19:00")["available_table_ids"]
    a3.post(f"/reservations/{blocker['reference']}/cancel", {}, token=bob)
    r = series(a3, ada, anchor["reference"], key="sk")
    assert r.status_code == 201
    assert r.json()["revision"] == 1


def test_first_failing_index_decides(a3, ada, bob, mgr):
    anchor = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    a3.book(bob, table_id="t_2", local=f"{THU3}T19:00")
    p = {"effective_from": THU2, "slot_minutes": 30, "reservation_duration_minutes": 90,
         "cancellation_cutoff_minutes": 60,
         "opening_hours": [{"weekday": "fri", "opens": "17:00", "closes": "23:00"}],
         "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}
    a3.post("/restaurants/r_anker/policies", p, token=mgr, key="closed-thu")
    assert_error(series(a3, ada, anchor["reference"]), 422, "outside_opening_hours")


def test_dst_nonexistent_rejects_whole(a3, ada):
    anchor = a3.book(ada, table_id="t_2", local="2030-03-24T02:30").json()
    assert_error(series(a3, ada, anchor["reference"], count=3), 422, "invalid_local_time")
    assert len(a3.get("/reservations", token=ada).json()["reservations"]) == 1


def test_dst_repeated_first_occurrence(a3, ada):
    anchor = a3.book(ada, table_id="t_2", local="2030-10-20T02:30").json()
    s = series(a3, ada, anchor["reference"], count=2).json()
    res = s["occurrences"][1]["reservation"]
    assert res["starts_at"] == "2030-10-27T02:30:00+02:00"


BAD = [{"count": 1}, {"count": 13}, {"count": True}, {"count": "4"}, {"count": 4.5},
       {"count": None}, {"interval_weeks": 0}, {"interval_weeks": 5},
       {"interval_weeks": False}, {"interval_weeks": "1"}, {"anchor_reference": 5},
       {"anchor_reference": None}]


@pytest.mark.parametrize("bad", BAD, ids=[repr(b) for b in BAD])
def test_series_validation(a3, ada, bad):
    anchor = a3.book(ada).json()
    body = {"anchor_reference": anchor["reference"], "count": 4, "interval_weeks": 1}
    body.update(bad)
    assert_error(a3.post("/series", body, token=ada, key=uuid.uuid4().hex), 422,
                 "validation_failed")


@pytest.mark.parametrize("missing", ["anchor_reference", "count", "interval_weeks"])
def test_series_missing(a3, ada, missing):
    anchor = a3.book(ada).json()
    body = {"anchor_reference": anchor["reference"], "count": 4, "interval_weeks": 1}
    del body[missing]
    assert_error(a3.post("/series", body, token=ada, key=uuid.uuid4().hex), 422,
                 "validation_failed")


def test_series_boundaries(a3, ada):
    a = a3.book(ada, table_id="t_1", local=f"{THU}T18:00").json()
    assert len(series(a3, ada, a["reference"], count=12, interval=1).json()["occurrences"]) == 12
    b = a3.book(ada, table_id="t_2", local=f"{THU}T18:00").json()
    assert len(series(a3, ada, b["reference"], count=2, interval=4).json()["occurrences"]) == 2


def test_series_anchor_errors(a3, ada, bob):
    a = a3.book(ada).json()
    assert_error(series(a3, bob, a["reference"]), 404, "not_found")
    assert_error(series(a3, ada, "NOPE0000"), 404, "not_found")
    assert_error(a3.post("/series", {"anchor_reference": a["reference"], "count": 2,
                                     "interval_weeks": 1}, key="x"), 401, "unauthenticated")
    assert_error(a3.post("/series", {"anchor_reference": a["reference"], "count": 2,
                                     "interval_weeks": 1}, token=ada), 400,
                 "missing_idempotency_key")
    c = a3.book(ada, table_id="t_3").json()
    a3.post(f"/reservations/{c['reference']}/cancel", {}, token=ada)
    assert_error(series(a3, ada, c["reference"]), 409, "reservation_cancelled")
    p = a3.book(ada, table_id="t_1", local=f"{PAST_THU}T19:00").json()
    assert_error(series(a3, ada, p["reference"]), 409, "cutoff_passed")
    s = series(a3, ada, a["reference"], count=2).json()
    assert_error(series(a3, ada, a["reference"], count=3), 409, "already_in_series")
    assert_error(series(a3, ada, s["occurrences"][1]["reference"], count=2), 409,
                 "already_in_series")


def test_series_read_access(a3, ada, bob):
    a = a3.book(ada).json()
    sid = series(a3, ada, a["reference"], count=2).json()["series_id"]
    assert_error(a3.get(f"/series/{sid}", token=bob), 404, "not_found")
    assert_error(a3.get(f"/series/{sid}"), 404, "not_found")
    assert_error(a3.get("/series/nope", token=ada), 404, "not_found")


def test_exception_and_cancel_counters(a3, ada):
    a = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    s = series(a3, ada, a["reference"], count=4).json()
    sid = s["series_id"]
    o1, o2, o3 = (s["occurrences"][i]["reference"] for i in (1, 2, 3))
    # no-op PATCH: nothing
    a3.patch(f"/reservations/{o1}", {"party_size": 2}, token=ada)
    # failed PATCH: nothing
    a3.patch(f"/reservations/{o1}", {"party_size": 99}, token=ada)
    g = a3.get(f"/series/{sid}", token=ada).json()
    assert g["revision"] == 1 and not any(o["exception"] for o in g["occurrences"])
    # real PATCH: exception + revision
    a3.patch(f"/reservations/{o1}", {"starts_at_local": f"{THU2}T20:00"}, token=ada)
    g = a3.get(f"/series/{sid}", token=ada).json()
    assert g["revision"] == 2
    assert [o["exception"] for o in g["occurrences"]] == [False, True, False, False]
    assert g["occurrences"][1]["reservation"]["starts_at_local"] == f"{THU2}T20:00"
    assert g["occurrences"][1]["reference"] == o1
    # cancel: revision, not an exception; repeat: nothing
    a3.post(f"/reservations/{o2}/cancel", {}, token=ada)
    a3.post(f"/reservations/{o2}/cancel", {}, token=ada)
    g = a3.get(f"/series/{sid}", token=ada).json()
    assert g["revision"] == 3
    assert [o["exception"] for o in g["occurrences"]] == [False, True, False, False]
    assert g["occurrences"][2]["reservation"]["status"] == "cancelled"
    # anchor cancel does not cancel siblings
    a3.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    g = a3.get(f"/series/{sid}", token=ada).json()
    assert g["revision"] == 4
    assert [o["reservation"]["status"] for o in g["occurrences"]] == [
        "cancelled", "confirmed", "cancelled", "confirmed"]
    # exception is permanent: changing it back keeps exception
    a3.patch(f"/reservations/{o1}", {"starts_at_local": f"{THU2}T19:00"}, token=ada)
    g = a3.get(f"/series/{sid}", token=ada).json()
    assert g["occurrences"][1]["exception"] is True and g["revision"] == 5


def test_series_replay(a3, ada):
    a = a3.book(ada).json()
    r1 = series(a3, ada, a["reference"], count=3, key="sr")
    o1 = r1.json()["occurrences"][1]["reference"]
    a3.patch(f"/reservations/{o1}", {"party_size": 1}, token=ada)
    r2 = series(a3, ada, a["reference"], count=3, key="sr")
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert a3.get(f"/series/{r1.json()['series_id']}", token=ada).json()["revision"] == 2
    assert_error(series(a3, ada, a["reference"], count=4, key="sr"), 409,
                 "idempotency_key_reuse")
    assert len(a3.get("/reservations", token=ada).json()["reservations"]) == 3


def test_series_concurrent_identical(a3, ada):
    a = a3.book(ada).json()
    rs = parallel([lambda: series(a3, ada, a["reference"], count=3, key="sc")] * 20)
    codes = [r.status_code for r in rs]
    assert codes.count(201) == 1 and codes.count(200) == 19, codes
    assert len(a3.get("/reservations", token=ada).json()["reservations"]) == 3


def test_moves_mark_exceptions_once(a3, ada):
    a = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    s = series(a3, ada, a["reference"], count=3).json()
    refs = [o["reference"] for o in s["occurrences"]]
    r = a3.post("/reservation-moves", {"moves": [
        {"reference": refs[1], "table_id": "t_3"},
        {"reference": refs[2], "table_id": "t_3"},
        {"reference": refs[0]}]}, token=ada, key="mx")
    assert r.status_code == 201, r.text
    g = a3.get(f"/series/{s['series_id']}", token=ada).json()
    assert g["revision"] == 2
    assert [o["exception"] for o in g["occurrences"]] == [False, True, True]
    # failed batch changes nothing
    r = a3.post("/reservation-moves", {"moves": [
        {"reference": refs[0], "table_id": "t_3"},
        {"reference": refs[1], "party_size": 99}]}, token=ada, key="my")
    assert r.status_code == 422
    g2 = a3.get(f"/series/{s['series_id']}", token=ada).json()
    assert g2 == g


def test_export_import_keeps_stage3_state(a3, ada, mgr):
    a3.post("/restaurants/r_anker/policies", {
        "effective_from": THU3, "slot_minutes": 15, "reservation_duration_minutes": 120,
        "cancellation_cutoff_minutes": 60,
        "opening_hours": [{"weekday": "thu", "opens": "17:00", "closes": "23:00"}],
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}, token=mgr, key="p1")
    a = a3.book(ada).json()
    s1 = series(a3, ada, a["reference"], count=3, key="s1")
    o1 = s1.json()["occurrences"][1]["reference"]
    a3.patch(f"/reservations/{o1}", {"party_size": 1}, token=ada)
    snap = a3.export()
    state = {
        "series": a3.get(f"/series/{s1.json()['series_id']}", token=ada).json(),
        "hist": a3.get(f"/reservations/{o1}/history", token=ada).json(),
        "policies": a3.get("/restaurants/r_anker/policies").json(),
    }
    a3.reset()
    assert a3.import_(snap).status_code == 204
    assert a3.get(f"/series/{s1.json()['series_id']}", token=ada).json() == state["series"]
    assert a3.get(f"/reservations/{o1}/history", token=ada).json() == state["hist"]
    assert a3.get("/restaurants/r_anker/policies").json() == state["policies"]
    r = series(a3, ada, a["reference"], count=3, key="s1")
    assert r.status_code == 200 and r.json() == s1.json()
    nxt = a3.post("/restaurants/r_anker/policies", {
        "effective_from": THU3, "slot_minutes": 15, "reservation_duration_minutes": 120,
        "cancellation_cutoff_minutes": 60, "opening_hours": [],
        "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}, token=mgr, key="p2")
    assert nxt.json()["policy_version"] == 2


# --- upgrades from earlier builds -------------------------------------------------------

def _populate_old(old):
    old.reset()
    ada = old.login()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00",
            "party_size": 2}
    r1 = old.post("/reservations", body, token=ada, key="old1").json()
    c = old.book(ada, table_id="t_1", local=f"{THU}T21:00").json()
    old.post(f"/reservations/{c['reference']}/cancel", {}, token=ada)
    return ada, body, r1, c


@pytest.mark.parametrize("n", [1, 2])
def test_upgrade_from_earlier_stage(api, n):
    url = os.environ.get(f"TK_PREV_URL_{n}")
    if not url:
        pytest.skip(f"TK_PREV_URL_{n} not set")
    old = Api(url)
    ada, body, r1, c = _populate_old(old)
    snap = old.export()
    old.client.close()
    assert api.import_(snap).status_code == 204
    g = api.get(f"/reservations/{r1['reference']}", token=ada).json()
    assert g["revision"] == 1
    assert g["accepted_terms"]["policy_version"] == 0
    assert g["accepted_terms"]["capacities"] == {"t_1": 2, "t_2": 4, "t_3": 6}
    h = api.get(f"/reservations/{r1['reference']}/history", token=ada).json()
    assert h["entries"][0]["event"] == "created"
    hc = api.get(f"/reservations/{c['reference']}/history", token=ada).json()
    assert hc["entries"][-1]["event"] == "cancelled"
    rp = api.post("/reservations", body, token=ada, key="old1")
    assert rp.status_code == 200
    for k, v in r1.items():
        assert rp.json()[k] == v
    s = series(api, ada, r1["reference"], count=3)
    assert s.status_code == 201, s.text
    assert s.json()["occurrences"][0]["reference"] == r1["reference"]
    p = api.patch(f"/reservations/{r1['reference']}", {"party_size": 3}, token=ada)
    assert p.status_code == 200 and p.json()["revision"] == 2
