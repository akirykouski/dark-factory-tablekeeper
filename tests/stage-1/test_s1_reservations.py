"""S1-50..S1-62: reservations create / list / get / cancel / amend."""
import uuid

import pytest

from conftest import (RES_KEYS, FRI, PAST_THU, REF_RE, SAT, THU, TS_RE, assert_error, base_fixture,
                      fresh_fixture)

TYPES = {"string": "x", "number": 1, "boolean": True, "null": None, "array": [], "object": {}}


def body(**kw):
    b = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00",
         "party_size": 4}
    b.update(kw)
    return b


def test_create_shape(api, ada):
    r = api.post("/reservations", body(), token=ada, key="k1")
    assert r.status_code == 201, r.text
    assert r.headers["content-type"].startswith("application/json")
    b = r.json()
    assert set(b) == RES_KEYS
    assert b["restaurant_id"] == "r_anker" and b["table_id"] == "t_2"
    assert b["party_size"] == 4 and b["status"] == "confirmed"
    assert b["starts_at_local"] == f"{THU}T19:00"
    assert b["starts_at"] == f"{THU}T19:00:00+02:00"
    assert b["ends_at"] == f"{THU}T20:30:00+02:00"
    assert TS_RE.match(b["created_at"])
    assert REF_RE.match(b["reference"])
    assert isinstance(b["reservation_id"], str) and len(b["reservation_id"]) <= 64


def test_references_unique(api, ada):
    refs = set()
    for i, t in enumerate(["18:00", "19:30", "21:00"]):
        for tbl in ["t_1", "t_2", "t_3"]:
            refs.add(api.book(ada, table_id=tbl, local=f"{THU}T{t}").json()["reference"])
    assert len(refs) == 9


def test_get_and_list(api, ada, bob):
    a = api.book(ada, local=f"{THU}T18:00").json()
    b = api.book(ada, local=f"{FRI}T20:00").json()
    c = api.book(ada, table_id="t_1", local=f"{THU}T21:30").json()
    api.book(bob, table_id="t_3", local=f"{THU}T18:00")
    lst = api.get("/reservations", token=ada).json()
    assert set(lst) == {"reservations"}
    assert [x["reference"] for x in lst["reservations"]] == [b["reference"], c["reference"],
                                                              a["reference"]]
    for x in lst["reservations"]:
        assert set(x) == RES_KEYS
    g = api.get(f"/reservations/{a['reference']}", token=ada)
    assert g.status_code == 200 and g.json() == a


def test_list_includes_cancelled(api, ada):
    a = api.book(ada).json()
    api.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    lst = api.get("/reservations", token=ada).json()["reservations"]
    assert [x["status"] for x in lst] == ["cancelled"]


def test_get_other_users_is_404(api, ada, bob):
    a = api.book(ada).json()
    assert_error(api.get(f"/reservations/{a['reference']}", token=bob), 404, "not_found")
    assert_error(api.get("/reservations/ZZZZZZZZ", token=bob), 404, "not_found")
    assert_error(api.post(f"/reservations/{a['reference']}/cancel", {}, token=bob), 404,
                 "not_found")
    assert_error(api.patch(f"/reservations/{a['reference']}", {"party_size": 1}, token=bob),
                 404, "not_found")
    # unchanged
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a


# --- create: rules --------------------------------------------------------------

@pytest.mark.parametrize("kw,status,code", [
    ({"table_id": "t_2", "starts_at_local": f"{THU}T19:15"}, 422, "not_on_slot_grid"),
    ({"starts_at_local": f"{THU}T17:30"}, 422, "outside_opening_hours"),
    ({"starts_at_local": f"{THU}T22:00"}, 422, "outside_opening_hours"),
    ({"starts_at_local": f"{THU}T23:00"}, 422, "outside_opening_hours"),
    ({"starts_at_local": f"{SAT}T19:00"}, 422, "outside_opening_hours"),
    ({"party_size": 5}, 422, "party_exceeds_capacity"),
    ({"restaurant_id": "nope"}, 404, "not_found"),
    ({"table_id": "nope"}, 404, "not_found"),
    ({"restaurant_id": "r_hudson", "table_id": "t_2"}, 404, "not_found"),
    ({"party_size": 0}, 422, "validation_failed"),
    ({"party_size": -2}, 422, "validation_failed"),
    ({"party_size": 2.5}, 422, "validation_failed"),
    ({"party_size": 4.0}, 422, "validation_failed"),
    ({"party_size": "4"}, 422, "validation_failed"),
    ({"party_size": True}, 422, "validation_failed"),
    ({"party_size": None}, 422, "validation_failed"),
    ({"party_size": []}, 422, "validation_failed"),
    ({"party_size": {}}, 422, "validation_failed"),
])
def test_create_rules(api, ada, kw, status, code):
    r = api.post("/reservations", body(**kw), token=ada, key=uuid.uuid4().hex)
    assert_error(r, status, code)
    assert api.get("/reservations", token=ada).json() == {"reservations": []}


def test_last_valid_slot_ok(api, ada):
    api.book(ada, local=f"{THU}T21:30")


def test_table_unavailable(api, ada, bob):
    api.book(ada, table_id="t_2", local=f"{THU}T19:00")
    r = api.book(bob, table_id="t_2", local=f"{THU}T19:30", expect=None)
    assert_error(r, 409, "table_unavailable")
    assert api.get("/reservations", token=bob).json() == {"reservations": []}


def test_capacity_before_overlap(api, ada):
    api.book(ada, table_id="t_1", local=f"{THU}T19:00")
    r = api.book(ada, table_id="t_1", local=f"{THU}T19:00", party_size=3, expect=None)
    assert_error(r, 422, "party_exceeds_capacity")


@pytest.mark.parametrize("field", ["restaurant_id", "table_id", "starts_at_local"])
@pytest.mark.parametrize("tname", ["number", "boolean", "null", "array", "object"])
def test_create_type_matrix(api, ada, field, tname):
    r = api.post("/reservations", body(**{field: TYPES[tname]}), token=ada,
                 key=uuid.uuid4().hex)
    assert_error(r, 400, "malformed_request")


@pytest.mark.parametrize("field", ["restaurant_id", "table_id", "starts_at_local", "party_size"])
def test_create_missing_field(api, ada, field):
    b = body()
    del b[field]
    r = api.post("/reservations", b, token=ada, key=uuid.uuid4().hex)
    assert_error(r, 422, "validation_failed")


@pytest.mark.parametrize("v", [f"{THU}T19:00:00", f"{THU}T19:00Z", f"{THU}T19:00+02:00",
                               f" {THU}T19:00", f"{THU}T19:00 ", f"{THU}T19:00\n",
                               f"{THU}\tT19:00", f"{THU} 19:00", f"{THU}T1900", f"{THU}T19:0",
                               f"{THU}T19:000", "2030-9-26T19:00", "2030-02-30T19:00",
                               f"{THU}T24:00", f"{THU}T19:60", "", "tomorrow", THU])
def test_bad_starts_at_local(api, ada, v):
    r = api.post("/reservations", body(starts_at_local=v), token=ada, key=uuid.uuid4().hex)
    assert_error(r, 422, "validation_failed")


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\"x\"", b"7", b"null", b"", b"{\"a\":}"])
def test_create_malformed(api, ada, raw):
    r = api.req("POST", "/reservations", token=ada, key="kk", content=raw)
    assert_error(r, 400, "malformed_request")


def test_unknown_fields_ignored(api, ada):
    r = api.post("/reservations", body(foo=1, status="cancelled", reference="AAAAAA"),
                 token=ada, key="x")
    assert r.status_code == 201
    assert r.json()["status"] == "confirmed"


def test_past_booking_allowed(api, ada):
    r = api.book(ada, local=f"{PAST_THU}T19:00")
    assert r.json()["starts_at"] == f"{PAST_THU}T19:00:00+01:00"


# --- cancel ------------------------------------------------------------------------

def test_cancel(api, ada):
    a = api.book(ada).json()
    r = api.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    assert r.status_code == 200
    c = r.json()
    assert set(c) == RES_KEYS
    assert c["status"] == "cancelled"
    for k in RES_KEYS - {"status", "revision"}:  # stage 3: cancel increments revision
        assert c[k] == a[k], k
    r2 = api.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    assert r2.status_code == 200 and r2.json() == c


def test_cancel_without_body(api, ada):
    a = api.book(ada).json()
    r = api.req("POST", f"/reservations/{a['reference']}/cancel", token=ada)
    assert r.status_code == 200


def test_cancel_cutoff(api, ada):
    a = api.book(ada, local=f"{PAST_THU}T19:00").json()
    r = api.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    assert_error(r, 409, "cutoff_passed")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json()["status"] == "confirmed"


def test_cancel_already_cancelled_seeded_past(api):
    # a cancelled booking returns 200 even past cutoff (cancelled check comes first)
    fx = fresh_fixture()
    fx["reservations"] = [{"id": "res_s1", "reference": "SEED01", "user_id": "u_ada",
                           "restaurant_id": "r_anker", "table_id": "t_1",
                           "starts_at_local": f"{THU}T19:00", "party_size": 2}]
    api.reset(fx)
    tok = api.login()
    r = api.post("/reservations/SEED01/cancel", {}, token=tok)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["reservation_id"] == "res_s1"


# --- seeded ---------------------------------------------------------------------

def test_seeded_reservation(api):
    fx = fresh_fixture()
    fx["reservations"] = [{"id": "res_seed", "reference": "SEED42", "user_id": "u_ada",
                           "restaurant_id": "r_anker", "table_id": "t_2",
                           "starts_at_local": f"{THU}T19:00", "party_size": 3}]
    api.reset(fx)
    tok = api.login()
    r = api.get("/reservations/SEED42", token=tok)
    assert r.status_code == 200
    b = r.json()
    assert set(b) == RES_KEYS
    assert b["reservation_id"] == "res_seed" and b["status"] == "confirmed"
    assert b["ends_at"] == f"{THU}T20:30:00+02:00"
    assert TS_RE.match(b["created_at"])
    assert "t_2" not in api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]
    bob = api.login("bob@example.com", "battery staple")
    assert_error(api.get("/reservations/SEED42", token=bob), 404, "not_found")
    r = api.book(bob, table_id="t_2", local=f"{THU}T20:00", expect=None)
    assert_error(r, 409, "table_unavailable")


# --- amend --------------------------------------------------------------------------

def test_patch_time_and_table(api, ada):
    a = api.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    r = api.patch(f"/reservations/{a['reference']}",
                  {"table_id": "t_3", "starts_at_local": f"{THU}T20:00"}, token=ada)
    assert r.status_code == 200, r.text
    p = r.json()
    assert set(p) == RES_KEYS
    assert p["reference"] == a["reference"] and p["reservation_id"] == a["reservation_id"]
    assert p["created_at"] == a["created_at"]
    assert p["table_id"] == "t_3" and p["starts_at"] == f"{THU}T20:00:00+02:00"
    assert p["ends_at"] == f"{THU}T21:30:00+02:00"
    assert "t_2" in api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]
    assert "t_3" not in api.slot("r_anker", THU, 2, f"{THU}T20:00")["available_table_ids"]
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == p


def test_patch_party_size_only(api, ada):
    a = api.book(ada, table_id="t_2", party_size=2).json()
    p = api.patch(f"/reservations/{a['reference']}", {"party_size": 4}, token=ada).json()
    assert p["party_size"] == 4 and p["table_id"] == "t_2"


def test_patch_own_interval_does_not_conflict(api, ada):
    a = api.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    r = api.patch(f"/reservations/{a['reference']}", {"starts_at_local": f"{THU}T19:30"},
                  token=ada)
    assert r.status_code == 200


def test_patch_noop_and_empty(api, ada):
    a = api.book(ada).json()
    r = api.patch(f"/reservations/{a['reference']}", {}, token=ada)
    assert r.status_code == 200 and r.json() == a
    r = api.patch(f"/reservations/{a['reference']}", {"table_id": "t_2", "party_size": 2},
                  token=ada)
    assert r.status_code == 200 and r.json() == a


@pytest.mark.parametrize("change,status,code", [
    ({"starts_at_local": f"{THU}T19:15"}, 422, "not_on_slot_grid"),
    ({"starts_at_local": f"{THU}T22:00"}, 422, "outside_opening_hours"),
    ({"party_size": 5}, 422, "party_exceeds_capacity"),
    ({"party_size": 0}, 422, "validation_failed"),
    ({"party_size": "3"}, 422, "validation_failed"),
    ({"starts_at_local": "2026-03-29T02:30"}, 422, "invalid_local_time"),
    ({"starts_at_local": f"{THU}T19:00Z"}, 422, "validation_failed"),
    ({"table_id": "nope"}, 404, "not_found"),
    ({"table_id": "h_1"}, 404, "not_found"),
    ({"table_id": 3}, 400, "malformed_request"),
    ({"starts_at_local": None}, 400, "malformed_request"),
    ({"table_id": "t_1"}, 409, "table_unavailable"),
])
def test_patch_failures_change_nothing(api, ada, change, status, code):
    a = api.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=2).json()
    api.book(ada, table_id="t_1", local=f"{THU}T19:30")
    r = api.patch(f"/reservations/{a['reference']}", change, token=ada)
    assert_error(r, status, code)
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a
    assert "t_2" not in api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]


def test_patch_cancelled(api, ada):
    a = api.book(ada).json()
    api.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    r = api.patch(f"/reservations/{a['reference']}", {"party_size": 1}, token=ada)
    assert_error(r, 409, "reservation_cancelled")


def test_patch_cutoff_current_start(api, ada):
    a = api.book(ada, local=f"{PAST_THU}T19:00").json()
    r = api.patch(f"/reservations/{a['reference']}", {"starts_at_local": f"{THU}T19:00"},
                  token=ada)
    assert_error(r, 409, "cutoff_passed")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a


def test_patch_cutoff_precedes_validation(api, ada):
    a = api.book(ada, local=f"{PAST_THU}T19:00").json()
    r = api.patch(f"/reservations/{a['reference']}", {"party_size": 99}, token=ada)
    assert_error(r, 409, "cutoff_passed")


def test_patch_malformed(api, ada):
    a = api.book(ada).json()
    r = api.req("PATCH", f"/reservations/{a['reference']}", token=ada, content=b"{nope")
    assert_error(r, 400, "malformed_request")


def test_patch_into_past_allowed(api, ada):
    a = api.book(ada).json()
    r = api.patch(f"/reservations/{a['reference']}", {"starts_at_local": f"{PAST_THU}T19:00"},
                  token=ada)
    assert r.status_code == 200
