"""S1-16, S1-34..S1-41, S1-56: idempotency and concurrency."""
import json
import uuid

import pytest

from conftest import THU, assert_error, parallel


def body(**kw):
    b = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00",
         "party_size": 2}
    b.update(kw)
    return b


@pytest.mark.parametrize("key", [None, ""])
def test_missing_key(api, ada, key):
    r = api.post("/reservations", body(), token=ada, key=key)
    assert_error(r, 400, "missing_idempotency_key")
    assert api.get("/reservations", token=ada).json() == {"reservations": []}


def test_key_length_bounds(api, ada):
    assert_error(api.post("/reservations", body(), token=ada, key="k" * 256), 422,
                 "validation_failed")
    assert api.post("/reservations", body(), token=ada, key="k" * 255).status_code == 201
    assert api.post("/reservations", body(table_id="t_3"), token=ada, key="z").status_code == 201


def test_auth_before_key(api):
    assert_error(api.post("/reservations", body()), 401, "unauthenticated")


def test_replay_identical(api, ada):
    r1 = api.post("/reservations", body(), token=ada, key="same")
    assert r1.status_code == 201
    raw = '{"party_size":2,  "starts_at_local":"%sT19:00","table_id":"t_2","restaurant_id":"r_anker"}' % THU
    r2 = api.req("POST", "/reservations", token=ada, key="same", content=raw.encode())
    assert r2.status_code == 200
    assert r2.json() == r1.json()
    assert len(api.get("/reservations", token=ada).json()["reservations"]) == 1


def test_reuse_different_body(api, ada):
    api.post("/reservations", body(), token=ada, key="k")
    assert_error(api.post("/reservations", body(party_size=3), token=ada, key="k"), 409,
                 "idempotency_key_reuse")
    # even an invalid new body
    assert_error(api.post("/reservations", body(party_size=0), token=ada, key="k"), 409,
                 "idempotency_key_reuse")
    assert_error(api.post("/reservations", {}, token=ada, key="k"), 409,
                 "idempotency_key_reuse")
    assert_error(api.post("/reservations", body(table_id=7), token=ada, key="k"), 409,
                 "idempotency_key_reuse")
    assert len(api.get("/reservations", token=ada).json()["reservations"]) == 1


def test_failed_key_is_reusable(api, ada):
    r = api.post("/reservations", body(party_size=9), token=ada, key="f")
    assert_error(r, 422, "party_exceeds_capacity")
    r = api.post("/reservations", body(), token=ada, key="f")
    assert r.status_code == 201
    r2 = api.post("/reservations", body(), token=ada, key="f")
    assert r2.status_code == 200 and r2.json() == r.json()


def test_failed_conflict_key_reusable_same_body(api, ada, bob):
    api.book(bob, table_id="t_2", local=f"{THU}T19:00")
    assert_error(api.post("/reservations", body(), token=ada, key="c"), 409, "table_unavailable")
    bobs = api.get("/reservations", token=bob).json()["reservations"]
    api.post(f"/reservations/{bobs[0]['reference']}/cancel", {}, token=bob)
    assert api.post("/reservations", body(), token=ada, key="c").status_code == 201


def test_key_scoped_per_user(api, ada, bob):
    a = api.post("/reservations", body(), token=ada, key="shared")
    b = api.post("/reservations", body(table_id="t_3"), token=bob, key="shared")
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["reference"] != b.json()["reference"]
    # same body by another user on a taken table is a real attempt, not a replay
    c = api.post("/reservations", body(), token=bob, key="shared2")
    assert_error(c, 409, "table_unavailable")


def test_same_key_other_path_not_replay(api, ada):
    a = api.post("/reservations", body(), token=ada, key="p")
    assert a.status_code == 201
    r = api.post("/reservation-moves", {"moves": [{"reference": a.json()["reference"]}]},
                 token=ada, key="p")
    assert r.status_code == 201, r.text


def test_replay_after_cancel_and_amend(api, ada):
    r1 = api.post("/reservations", body(), token=ada, key="r")
    ref = r1.json()["reference"]
    api.patch(f"/reservations/{ref}", {"party_size": 3}, token=ada)
    api.post(f"/reservations/{ref}/cancel", {}, token=ada)
    r2 = api.post("/reservations", body(), token=ada, key="r")
    assert r2.status_code == 200 and r2.json() == r1.json()
    cur = api.get(f"/reservations/{ref}", token=ada).json()
    assert cur["status"] == "cancelled" and cur["party_size"] == 3
    # the replay did not re-occupy the table
    assert "t_2" in api.slot("r_anker", THU, 2, f"{THU}T19:00")["available_table_ids"]


def test_replay_works_with_other_token_same_user(api, ada):
    r1 = api.post("/reservations", body(), token=ada, key="tok")
    t2 = api.login()
    r2 = api.post("/reservations", body(), token=t2, key="tok")
    assert r2.status_code == 200 and r2.json() == r1.json()


def test_concurrent_identical_requests(api, ada):
    rs = parallel([lambda: api.post("/reservations", body(), token=ada, key="burst")] * 30)
    codes = sorted(r.status_code for r in rs)
    assert codes.count(201) == 1, codes
    assert codes.count(200) == 29, codes
    first = rs[[r.status_code for r in rs].index(201)].json()
    assert all(r.json() == first for r in rs)
    assert len(api.get("/reservations", token=ada).json()["reservations"]) == 1


def test_concurrent_competing_bookings(api, ada):
    rs = parallel([lambda: api.post("/reservations", body(), token=ada, key=uuid.uuid4().hex)
                   for _ in range(40)])
    codes = [r.status_code for r in rs]
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 39, codes
    for r in rs:
        if r.status_code == 409:
            assert r.json()["error"]["code"] == "table_unavailable"


def test_concurrent_overlapping_different_slots(api, ada):
    slots = ["18:00", "18:30", "19:00", "19:30", "20:00"]
    rs = parallel([lambda s=s: api.post("/reservations", body(starts_at_local=f"{THU}T{s}"),
                                        token=ada, key=uuid.uuid4().hex)
                   for s in slots for _ in range(6)])
    won = sorted(r.json()["starts_at_local"] for r in rs if r.status_code == 201)
    # no two winners overlap (90 min duration => starts at least 90 apart)
    mins = [int(w[11:13]) * 60 + int(w[14:16]) for w in won]
    for a, b in zip(mins, mins[1:]):
        assert b - a >= 90, won
    assert all(r.status_code in (201, 409) for r in rs)


def test_concurrent_patches_into_same_slot(api, ada):
    refs = [api.book(ada, table_id="t_3", local=f"{THU}T{t}").json()["reference"]
            for t in ["18:00", "21:30"]]
    refs += [api.book(ada, table_id="t_1", local=f"{THU}T{t}").json()["reference"]
             for t in ["18:00", "21:30"]]
    rs = parallel([lambda r=r: api.patch(f"/reservations/{r}",
                                         {"table_id": "t_2", "starts_at_local": f"{THU}T20:00"},
                                         token=ada) for r in refs])
    codes = sorted(r.status_code for r in rs)
    assert codes == [200, 409, 409, 409], codes


def test_mixed_load_no_5xx(api, ada):
    fns = []
    for i in range(50):
        if i % 3 == 0:
            fns.append(lambda: api.avail())
        elif i % 3 == 1:
            fns.append(lambda: api.get("/reservations", token=ada))
        else:
            fns.append(lambda: api.post("/reservations", body(table_id="t_1"), token=ada,
                                        key=uuid.uuid4().hex))
    rs = parallel(fns)
    assert all(r.status_code < 500 for r in rs)
