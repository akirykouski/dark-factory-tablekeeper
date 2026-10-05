"""S1-67..S1-82: export/import and atomic reservation moves."""
import copy
import uuid

import pytest

from conftest import PASSWORD, PAST_THU, THU, assert_error, base_fixture, parallel


def mv(api, token, moves, key=None):
    return api.post("/reservation-moves", {"moves": moves}, token=token,
                    key=key or uuid.uuid4().hex)


@pytest.fixture
def two(api, ada):
    a = api.book(ada, table_id="t_1", local=f"{THU}T19:00").json()
    b = api.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    return a, b


# --- moves ------------------------------------------------------------------------

def test_swap(api, ada, two):
    a, b = two
    r = mv(api, ada, [{"reference": a["reference"], "table_id": "t_2"},
                      {"reference": b["reference"], "table_id": "t_1"}])
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"reservations"}
    out = body["reservations"]
    assert [x["reference"] for x in out] == [a["reference"], b["reference"]]
    assert out[0]["table_id"] == "t_2" and out[1]["table_id"] == "t_1"
    for x, orig in zip(out, [a, b]):
        assert x["reservation_id"] == orig["reservation_id"]
        assert x["created_at"] == orig["created_at"]
        assert set(x) == set(orig)
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == out[0]


def test_move_requires_key_and_auth(api, ada, two):
    a, _ = two
    assert_error(api.post("/reservation-moves", {"moves": [{"reference": a["reference"]}]},
                          token=ada), 400, "missing_idempotency_key")
    assert_error(api.post("/reservation-moves", {"moves": [{"reference": a["reference"]}]},
                          key="x"), 401, "unauthenticated")


@pytest.mark.parametrize("moves", [None, "x", 5, {}, [], [1], ["ABC"], [{}],
                                   [{"reference": 5}], [{"reference": None}],
                                   [{"table_id": "t_1"}]])
def test_move_shape(api, ada, moves):
    b = {} if moves is None else {"moves": moves}
    r = api.post("/reservation-moves", b, token=ada, key=uuid.uuid4().hex)
    assert_error(r, 422, "validation_failed")


def test_move_nine_items(api, ada):
    refs = []
    for t in ["t_1", "t_2", "t_3"]:
        for s in ["18:00", "19:30", "21:00"]:
            refs.append(api.book(ada, table_id=t, local=f"{THU}T{s}").json()["reference"])
    assert_error(mv(api, ada, [{"reference": r} for r in refs]), 422, "validation_failed")
    assert mv(api, ada, [{"reference": r} for r in refs[:8]]).status_code == 201


def test_move_duplicate_refs(api, ada, two):
    a, _ = two
    assert_error(mv(api, ada, [{"reference": a["reference"]}, {"reference": a["reference"]}]),
                 422, "validation_failed")


def test_move_unknown_and_foreign(api, ada, bob, two):
    a, _ = two
    assert_error(mv(api, ada, [{"reference": a["reference"], "table_id": "t_3"},
                               {"reference": "NOPE99"}]), 404, "not_found")
    assert_error(mv(api, bob, [{"reference": a["reference"], "table_id": "t_3"}]), 404,
                 "not_found")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a


def test_move_different_restaurants(api, ada, two):
    a, _ = two
    h = api.book(ada, restaurant_id="r_hudson", table_id="h_1",
                 local="2030-09-26T19:00").json()
    assert_error(mv(api, ada, [{"reference": a["reference"]}, {"reference": h["reference"]}]),
                 422, "validation_failed")


def test_move_cancelled(api, ada, two):
    a, b = two
    api.post(f"/reservations/{b['reference']}/cancel", {}, token=ada)
    assert_error(mv(api, ada, [{"reference": a["reference"], "table_id": "t_3"},
                               {"reference": b["reference"], "table_id": "t_1"}]),
                 409, "reservation_cancelled")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a


def test_move_cutoff(api, ada, two):
    a, _ = two
    p = api.book(ada, table_id="t_2", local=f"{PAST_THU}T19:00").json()
    assert_error(mv(api, ada, [{"reference": a["reference"], "table_id": "t_3"},
                               {"reference": p["reference"], "table_id": "t_1"}]),
                 409, "cutoff_passed")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a


def test_move_input_order_precedence(api, ada, two):
    a, b = two
    r = mv(api, ada, [{"reference": a["reference"], "party_size": 99},
                      {"reference": b["reference"], "starts_at_local": f"{THU}T19:15"}])
    assert_error(r, 422, "party_exceeds_capacity")
    r = mv(api, ada, [{"reference": b["reference"], "starts_at_local": f"{THU}T19:15"},
                      {"reference": a["reference"], "party_size": 99}])
    assert_error(r, 422, "not_on_slot_grid")


def test_move_conflict_with_unlisted(api, ada, two):
    a, b = two
    r = mv(api, ada, [{"reference": a["reference"], "table_id": "t_2"}])
    assert_error(r, 409, "table_unavailable")


def test_move_conflict_among_results_is_atomic(api, ada, two):
    a, b = two
    c = api.book(ada, table_id="t_3", local=f"{THU}T21:00").json()
    r = mv(api, ada, [{"reference": c["reference"], "starts_at_local": f"{THU}T18:00",
                       "table_id": "t_3"},
                      {"reference": a["reference"], "table_id": "t_3"}])
    assert_error(r, 409, "table_unavailable")
    for orig in (a, b, c):
        assert api.get(f"/reservations/{orig['reference']}", token=ada).json() == orig
    assert "t_3" in api.slot("r_anker", THU, 2, f"{THU}T18:00")["available_table_ids"]


def test_move_noop_and_unchanged_retains_occupancy(api, ada, two):
    a, b = two
    r = mv(api, ada, [{"reference": a["reference"]},
                      {"reference": b["reference"], "table_id": "t_2", "party_size": 2,
                       "bogus": 1}])
    assert r.status_code == 201
    assert r.json()["reservations"] == [a, b]
    s = api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]
    assert s == ["t_3"]


def test_move_time_and_party(api, ada, two):
    a, b = two
    r = mv(api, ada, [{"reference": a["reference"], "starts_at_local": f"{THU}T21:00",
                       "party_size": 1}])
    assert r.status_code == 201
    x = r.json()["reservations"][0]
    assert x["starts_at_local"] == f"{THU}T21:00" and x["party_size"] == 1
    assert x["ends_at"] == f"{THU}T22:30:00+02:00"


def test_move_replay_and_reuse(api, ada, two):
    a, b = two
    moves = [{"reference": a["reference"], "table_id": "t_2"},
             {"reference": b["reference"], "table_id": "t_1"}]
    r1 = mv(api, ada, moves, key="m1")
    api.post(f"/reservations/{a['reference']}/cancel", {}, token=ada)
    r2 = mv(api, ada, moves, key="m1")
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert_error(mv(api, ada, moves[:1], key="m1"), 409, "idempotency_key_reuse")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json()["status"] == "cancelled"


def test_move_failed_key_reusable(api, ada, two):
    a, b = two
    assert_error(mv(api, ada, [{"reference": a["reference"], "table_id": "t_2"}], key="mf"),
                 409, "table_unavailable")
    r = mv(api, ada, [{"reference": a["reference"], "table_id": "t_3"}], key="mf")
    assert r.status_code == 201


def test_move_concurrent_identical(api, ada, two):
    a, b = two
    moves = [{"reference": a["reference"], "table_id": "t_2"},
             {"reference": b["reference"], "table_id": "t_1"}]
    rs = parallel([lambda: mv(api, ada, moves, key="mc")] * 20)
    codes = [r.status_code for r in rs]
    assert codes.count(201) == 1 and codes.count(200) == 19, codes


def test_move_types(api, ada, two):
    a, _ = two
    assert_error(mv(api, ada, [{"reference": a["reference"], "table_id": 1}]), 400,
                 "malformed_request")
    assert_error(mv(api, ada, [{"reference": a["reference"], "party_size": "2"}]), 422,
                 "validation_failed")


# --- export / import -----------------------------------------------------------------

def test_export_shape(api):
    e = api.export()
    assert e["track"] == "tablekeeper" and e["format_version"] == 1
    assert isinstance(e["state"], dict)


def test_roundtrip_preserves_everything(api, ada, bob):
    r1 = api.post("/reservations", {"restaurant_id": "r_anker", "table_id": "t_2",
                                    "starts_at_local": f"{THU}T19:00", "party_size": 2},
                  token=ada, key="ex1")
    ref = r1.json()["reference"]
    a2 = api.book(ada, table_id="t_1", local=f"{THU}T19:00").json()
    mvr = mv(api, ada, [{"reference": a2["reference"], "table_id": "t_3"}], key="mvx")
    api.post("/reservations", {"restaurant_id": "r_anker", "table_id": "t_2",
                               "starts_at_local": f"{THU}T19:30", "party_size": 2},
             token=ada, key="failed")
    signup = api.signup(email="new@example.com", password="newpassword")
    snap = api.export()
    before_list = api.get("/reservations", token=ada).json()

    api.reset()  # wipe destination, then import
    assert api.import_(snap).status_code == 204
    # tokens survive
    assert api.get("/reservations", token=ada).json() == before_list
    assert api.get("/reservations", token=signup["token"]).status_code == 200
    # hashed-password login survives
    api.login("new@example.com", "newpassword")
    api.login()
    # replay survives
    r2 = api.post("/reservations", {"restaurant_id": "r_anker", "table_id": "t_2",
                                    "starts_at_local": f"{THU}T19:00", "party_size": 2},
                  token=ada, key="ex1")
    assert r2.status_code == 200 and r2.json() == r1.json()
    assert_error(api.post("/reservations", {"restaurant_id": "r_anker", "table_id": "t_3",
                                            "starts_at_local": f"{THU}T21:00",
                                            "party_size": 2}, token=ada, key="ex1"),
                 409, "idempotency_key_reuse")
    m2 = mv(api, ada, [{"reference": a2["reference"], "table_id": "t_3"}], key="mvx")
    assert m2.status_code == 200 and m2.json() == mvr.json()
    # failed key still reusable
    r3 = api.post("/reservations", {"restaurant_id": "r_anker", "table_id": "t_1",
                                    "starts_at_local": f"{THU}T21:00", "party_size": 2},
                  token=ada, key="failed")
    assert r3.status_code == 201
    # new references stay unique vs imported ones
    assert r3.json()["reference"] not in {x["reference"] for x in before_list["reservations"]}
    # occupancy survives
    assert_error(api.book(bob, table_id="t_2", local=f"{THU}T19:00", expect=None), 409,
                 "table_unavailable")


def test_import_replaces_not_merges(api, ada):
    snap = api.export()
    api.book(ada)
    t = api.signup(email="gone@example.com")["token"]
    assert api.import_(snap).status_code == 204
    assert api.get("/reservations", token=ada).json() == {"reservations": []}
    assert_error(api.get("/reservations", token=t), 401, "unauthenticated")
    assert api.import_(snap).status_code == 204
    assert api.import_(snap).status_code == 204
    assert api.get("/reservations", token=ada).json() == {"reservations": []}


def test_import_twice_no_duplicates(api, ada):
    api.book(ada)
    snap = api.export()
    api.import_(snap)
    api.import_(snap)
    assert len(api.get("/reservations", token=ada).json()["reservations"]) == 1


def test_export_is_snapshot(api, ada):
    snap = api.export()
    frozen = copy.deepcopy(snap)
    api.book(ada)
    assert snap == frozen
    api.import_(snap)
    assert api.get("/reservations", token=ada).json() == {"reservations": []}


@pytest.mark.parametrize("mutate", [
    lambda e: {k: v for k, v in e.items() if k != "track"},
    lambda e: {k: v for k, v in e.items() if k != "format_version"},
    lambda e: {k: v for k, v in e.items() if k != "state"},
    lambda e: {**e, "track": "pocketful"},
    lambda e: {**e, "format_version": 2},
    lambda e: {**e, "format_version": "1"},
    lambda e: {**e, "state": "nope"},
    lambda e: {**e, "state": []},
    lambda e: {**e, "state": {"garbage": True}},
    lambda e: {},
])
def test_invalid_import_changes_nothing(api, ada, mutate):
    a = api.book(ada).json()
    snap = api.export()
    r = api.import_(mutate(snap))
    assert_error(r, 422, "validation_failed")
    assert api.get(f"/reservations/{a['reference']}", token=ada).json() == a


@pytest.mark.parametrize("raw", [b"{", b"[]", b"nope"])
def test_import_malformed(api, raw):
    r = api.req("POST", "/_test/import", content=raw)
    assert r.status_code in (400, 422)
    if raw != b"[]":
        assert_error(r, 400, "malformed_request")


def test_reset_clears_imported_state(api, ada):
    api.book(ada)
    snap = api.export()
    api.import_(snap)
    api.reset()
    assert_error(api.get("/reservations", token=ada), 401, "unauthenticated")
