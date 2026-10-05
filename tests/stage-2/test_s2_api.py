"""S2-01..S2-25: combined tables over the API, and upgrades from the previous stage's build."""
import copy
import os
import uuid

import pytest

from conftest import (RES_KEYS, SLOT_KEYS, THU, PAST_THU, Api, assert_error, base_fixture,
                      parallel)


def fx2():
    fx = copy.deepcopy(base_fixture())
    r = fx["restaurants"][0]
    r["tables"] = [{"id": "t_1", "label": "Window 1", "capacity": 2},
                   {"id": "t_2", "label": "Garden 2", "capacity": 4},
                   {"id": "t_3", "label": "Booth 3", "capacity": 6},
                   {"id": "t_4", "label": "Bar 4", "capacity": 2}]
    r["combinable"] = [["t_1", "t_2"], ["t_3", "t_2"]]
    return fx


@pytest.fixture
def api2(api):
    api.reset(fx2())
    return api


@pytest.fixture
def ada2(api2):
    return api2.login()


@pytest.fixture
def bob2(api2):
    return api2.login("bob@example.com", "battery staple")


def book_ids(api, tok, ids, local=f"{THU}T19:00", party=2, key=None):
    return api.post("/reservations", {"restaurant_id": "r_anker", "table_ids": ids,
                                      "starts_at_local": local, "party_size": party},
                    token=tok, key=key or uuid.uuid4().hex)


def test_detail_has_combinable(api2):
    d = api2.get("/restaurants/r_anker").json()
    assert d["combinable"] == [["t_1", "t_2"], ["t_3", "t_2"]]
    assert api2.get("/restaurants/r_hudson").json()["combinable"] == []


def test_available_options_order_and_capacity(api2):
    s = api2.slot("r_anker", THU, 2, f"{THU}T19:00")
    assert set(s) == SLOT_KEYS
    assert s["available_table_ids"] == ["t_1", "t_2", "t_3", "t_4"]
    assert s["available_options"] == [
        {"table_ids": ["t_1"], "capacity": 2}, {"table_ids": ["t_2"], "capacity": 4},
        {"table_ids": ["t_3"], "capacity": 6}, {"table_ids": ["t_4"], "capacity": 2},
        {"table_ids": ["t_1", "t_2"], "capacity": 6},
        {"table_ids": ["t_3", "t_2"], "capacity": 10}]
    s = api2.slot("r_anker", THU, 7, f"{THU}T19:00")
    assert s["available_table_ids"] == []
    assert s["available_options"] == [{"table_ids": ["t_3", "t_2"], "capacity": 10}]
    s = api2.slot("r_anker", THU, 11, f"{THU}T19:00")
    assert s["available_options"] == [] and s["available_table_ids"] == []


def test_options_respect_member_occupancy(api2, ada2):
    api2.book(ada2, table_id="t_2", local=f"{THU}T19:00")
    s = api2.slot("r_anker", THU, 2, f"{THU}T19:30")
    assert s["available_table_ids"] == ["t_1", "t_3", "t_4"]
    assert [o["table_ids"] for o in s["available_options"]] == [["t_1"], ["t_3"], ["t_4"]]


def test_pair_booking_shape_and_occupancy(api2, ada2):
    r = book_ids(api2, ada2, ["t_2", "t_1"], party=6)
    assert r.status_code == 201, r.text
    b = r.json()
    assert set(b) == RES_KEYS - {"table_id"}
    assert b["table_ids"] == ["t_1", "t_2"]
    s = api2.slot("r_anker", THU, 1, f"{THU}T19:00")
    assert s["available_table_ids"] == ["t_3", "t_4"]
    assert [o["table_ids"] for o in s["available_options"]] == [["t_3"], ["t_4"]]
    g = api2.get(f"/reservations/{b['reference']}", token=ada2).json()
    assert g == b
    lst = api2.get("/reservations", token=ada2).json()["reservations"]
    assert lst == [b]
    c = api2.post(f"/reservations/{b['reference']}/cancel", {}, token=ada2).json()
    assert c["table_ids"] == ["t_1", "t_2"] and "table_id" not in c
    s = api2.slot("r_anker", THU, 1, f"{THU}T19:00")
    assert s["available_table_ids"] == ["t_1", "t_2", "t_3", "t_4"]


def test_single_booking_carries_both_keys(api2, ada2):
    b = api2.book(ada2, table_id="t_2").json()
    assert set(b) == RES_KEYS
    assert b["table_id"] == "t_2" and b["table_ids"] == ["t_2"]
    b = book_ids(api2, ada2, ["t_3"]).json()
    assert b["table_id"] == "t_3" and b["table_ids"] == ["t_3"]


@pytest.mark.parametrize("ids,status,code", [
    (["t_1", "t_3"], 422, "combination_not_allowed"),
    (["t_1", "t_4"], 422, "combination_not_allowed"),
    (["t_1", "t_2", "t_3"], 422, "combination_not_allowed"),
    (["t_1", "t_1"], 422, "validation_failed"),
    (["t_1", "t_1", "t_2"], 422, "validation_failed"),
    ([], 422, "validation_failed"),
    (["t_1", "nope"], 404, "not_found"),
    (["t_1", "h_1"], 404, "not_found"),
    ("t_1", 400, "malformed_request"),
    ([1, 2], 400, "malformed_request"),
    (["t_1", None], 400, "malformed_request"),
    ({"a": "t_1"}, 400, "malformed_request"),
    (None, 400, "malformed_request"),
    (True, 400, "malformed_request"),
])
def test_pair_validation(api2, ada2, ids, status, code):
    r = book_ids(api2, ada2, ids, party=2)
    assert_error(r, status, code)
    assert api2.get("/reservations", token=ada2).json() == {"reservations": []}


def test_both_and_neither(api2, ada2):
    r = api2.post("/reservations", {"restaurant_id": "r_anker", "table_id": "t_1",
                                    "table_ids": ["t_1"], "starts_at_local": f"{THU}T19:00",
                                    "party_size": 2}, token=ada2, key="b")
    assert_error(r, 422, "validation_failed")
    r = api2.post("/reservations", {"restaurant_id": "r_anker",
                                    "starts_at_local": f"{THU}T19:00", "party_size": 2},
                  token=ada2, key="n")
    assert_error(r, 422, "validation_failed")


def test_pair_capacity_and_conflict(api2, ada2, bob2):
    assert_error(book_ids(api2, ada2, ["t_1", "t_2"], party=7), 422, "party_exceeds_capacity")
    assert book_ids(api2, ada2, ["t_1", "t_2"], party=6).status_code == 201
    assert_error(book_ids(api2, bob2, ["t_3", "t_2"], local=f"{THU}T20:00"), 409,
                 "table_unavailable")
    assert_error(api2.book(bob2, table_id="t_1", local=f"{THU}T18:00", expect=None), 409,
                 "table_unavailable")
    assert api2.book(bob2, table_id="t_3", local=f"{THU}T19:00").status_code == 201


def test_pair_rule_order(api2, ada2):
    # combination check precedes grid / hours / capacity
    assert_error(book_ids(api2, ada2, ["t_1", "t_3"], local=f"{THU}T19:15", party=99), 422,
                 "combination_not_allowed")
    assert_error(book_ids(api2, ada2, ["t_1", "t_2"], local=f"{THU}T19:15", party=99), 422,
                 "not_on_slot_grid")


def test_pair_replay(api2, ada2):
    r1 = book_ids(api2, ada2, ["t_1", "t_2"], party=5, key="pk")
    r2 = book_ids(api2, ada2, ["t_1", "t_2"], party=5, key="pk")
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert "table_id" not in r2.json()


def test_patch_single_to_pair_and_back(api2, ada2):
    b = api2.book(ada2, table_id="t_2", party_size=4).json()
    r = api2.patch(f"/reservations/{b['reference']}", {"table_ids": ["t_2", "t_3"],
                                                       "party_size": 9}, token=ada2)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["table_ids"] == ["t_3", "t_2"] and "table_id" not in p
    assert set(api2.slot("r_anker", THU, 1, f"{THU}T19:00")["available_table_ids"]) == \
        {"t_1", "t_4"}
    r = api2.patch(f"/reservations/{b['reference']}", {"table_id": "t_1", "party_size": 2},
                   token=ada2)
    assert r.status_code == 200
    assert r.json()["table_ids"] == ["t_1"] and r.json()["table_id"] == "t_1"
    assert api2.slot("r_anker", THU, 1, f"{THU}T19:00")["available_table_ids"] == \
        ["t_2", "t_3", "t_4"]


def test_patch_pair_failures(api2, ada2):
    b = api2.book(ada2, table_id="t_2", party_size=4).json()
    api2.book(ada2, table_id="t_1", local=f"{THU}T20:00")
    for change, status, code in [
        ({"table_ids": ["t_1", "t_2"]}, 409, "table_unavailable"),
        ({"table_ids": ["t_1", "t_3"]}, 422, "combination_not_allowed"),
        ({"table_ids": ["t_2", "t_3"], "table_id": "t_2"}, 422, "validation_failed"),
        ({"table_ids": ["t_2", "t_3"], "party_size": 11}, 422, "party_exceeds_capacity"),
        ({"table_ids": "t_2"}, 400, "malformed_request"),
    ]:
        assert_error(api2.patch(f"/reservations/{b['reference']}", change, token=ada2),
                     status, code)
        assert api2.get(f"/reservations/{b['reference']}", token=ada2).json() == b


def test_moves_with_table_ids(api2, ada2):
    a = api2.book(ada2, table_id="t_1").json()
    b = api2.book(ada2, table_id="t_3").json()
    r = api2.post("/reservation-moves", {"moves": [
        {"reference": a["reference"], "table_ids": ["t_3"]},
        {"reference": b["reference"], "table_ids": ["t_2", "t_1"], "party_size": 6}]},
        token=ada2, key="mv")
    assert r.status_code == 201, r.text
    out = r.json()["reservations"]
    assert out[0]["table_ids"] == ["t_3"] and out[0]["table_id"] == "t_3"
    assert out[1]["table_ids"] == ["t_1", "t_2"] and "table_id" not in out[1]


def test_moves_member_overlap_rejected(api2, ada2):
    a = api2.book(ada2, table_id="t_4").json()
    b = api2.book(ada2, table_id="t_3").json()
    r = api2.post("/reservation-moves", {"moves": [
        {"reference": a["reference"], "table_ids": ["t_1"]},
        {"reference": b["reference"], "table_ids": ["t_1", "t_2"]}]}, token=ada2, key="mo")
    assert_error(r, 409, "table_unavailable")
    assert api2.get(f"/reservations/{a['reference']}", token=ada2).json() == a
    assert api2.get(f"/reservations/{b['reference']}", token=ada2).json() == b


def test_seeded_pair_and_cancelled(api2):
    fx = fx2()
    fx["reservations"] = [
        {"id": "s1", "reference": "PAIR01", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_ids": ["t_2", "t_1"], "starts_at_local": f"{THU}T19:00", "party_size": 5},
        {"id": "s2", "reference": "GONE01", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_id": "t_3", "starts_at_local": f"{THU}T19:00", "party_size": 2,
         "status": "cancelled"},
    ]
    api2.reset(fx)
    tok = api2.login()
    p = api2.get("/reservations/PAIR01", token=tok).json()
    assert p["table_ids"] == ["t_1", "t_2"] and "table_id" not in p and p["status"] == "confirmed"
    g = api2.get("/reservations/GONE01", token=tok).json()
    assert g["status"] == "cancelled" and g["table_id"] == "t_3"
    assert api2.slot("r_anker", THU, 1, f"{THU}T19:00")["available_table_ids"] == ["t_3", "t_4"]


def test_concurrent_pair_vs_singles(api2, ada2):
    fns = [lambda: book_ids(api2, ada2, ["t_1", "t_2"], party=3)] * 10
    fns += [lambda: api2.book(ada2, table_id="t_1", expect=None)] * 10
    fns += [lambda: api2.book(ada2, table_id="t_2", expect=None)] * 10
    rs = parallel(fns)
    won = [r.json() for r in rs if r.status_code == 201]
    used = [t for w in won for t in w["table_ids"]]
    assert len(used) == len(set(used)), won
    assert all(r.status_code in (201, 409) for r in rs)
    assert len(won) in (1, 2)


# --- upgrades ---------------------------------------------------------------------

PREV = os.environ.get("TK_PREV_URL_1")


@pytest.mark.skipif(not PREV, reason="previous-stage build not started (TK_PREV_URL_1)")
def test_upgrade_from_stage1(api):
    old = Api(PREV)
    old.reset()
    ada = old.login()
    bob = old.login("bob@example.com", "battery staple")
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00",
            "party_size": 2}
    r1 = old.post("/reservations", body, token=ada, key="up1")
    ref1 = r1.json()["reference"]
    c = old.book(ada, table_id="t_1", local=f"{THU}T21:00").json()
    old.post(f"/reservations/{c['reference']}/cancel", {}, token=ada)
    m = old.book(bob, table_id="t_3", local=f"{THU}T19:00").json()
    mv1 = old.post("/reservation-moves", {"moves": [{"reference": m["reference"],
                                                     "starts_at_local": f"{THU}T18:00"}]},
                   token=bob, key="upm")
    old.post("/reservations", {**body, "table_id": "t_3"}, token=ada, key="upfail")  # 409
    signup = old.signup(email="late@example.com", password="latepassword")
    snap = old.export()
    old_list = old.get("/reservations", token=ada).json()["reservations"]
    old.client.close()

    assert api.import_(snap).status_code == 204
    lst = api.get("/reservations", token=ada).json()["reservations"]
    assert [x["reference"] for x in lst] == [x["reference"] for x in old_list]
    for new, prev in zip(lst, old_list):
        for k, v in prev.items():
            assert new[k] == v, k
        assert new["table_ids"] == [prev["table_id"]]
    r2 = api.post("/reservations", body, token=ada, key="up1")
    assert r2.status_code == 200
    for k, v in r1.json().items():
        assert r2.json()[k] == v
    assert_error(api.post("/reservations", {**body, "party_size": 3}, token=ada, key="up1"),
                 409, "idempotency_key_reuse")
    m2 = api.post("/reservation-moves", {"moves": [{"reference": m["reference"],
                                                    "starts_at_local": f"{THU}T18:00"}]},
                  token=bob, key="upm")
    assert m2.status_code == 200
    assert_error(api.post("/reservations", {**body, "table_id": "t_3"}, token=ada,
                          key="upfail"), 409, "table_unavailable")
    api.login("late@example.com", "latepassword")
    assert api.get("/reservations", token=signup["token"]).status_code == 200
    assert_error(api.book(bob, table_id="t_2", local=f"{THU}T19:30", expect=None), 409,
                 "table_unavailable")
    # imported confirmed booking can be amended into a pair? (no combinable in stage-1 data)
    p = api.patch(f"/reservations/{ref1}", {"party_size": 3}, token=ada)
    assert p.status_code == 200 and p.json()["reference"] == ref1
    # new references never collide with imported ones
    n = api.book(ada, table_id="t_1", local=f"{THU}T18:00").json()
    assert n["reference"] not in {x["reference"] for x in old_list}
