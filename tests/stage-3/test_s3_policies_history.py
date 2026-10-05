"""S3-01..S3-33, S3-44: explanations, history, policies, accepted terms, revisions."""
import copy
import uuid

import pytest

from conftest import (PAST_THU, RES_KEYS, SLOT_KEYS, THU, FRI, TS_RE, Api, assert_error,
                      base_fixture, parallel)

POLICY_KEYS = {"effective_from", "slot_minutes", "reservation_duration_minutes",
               "cancellation_cutoff_minutes", "opening_hours", "capacities", "policy_version"}
TERMS_KEYS = POLICY_KEYS - {"effective_from"}


def fx3():
    fx = copy.deepcopy(base_fixture())
    fx["users"].append({"id": "u_mgr", "email": "mgr@example.com", "password": "manager pass",
                        "display_name": "Manager"})
    r = fx["restaurants"][0]
    r["combinable"] = [["t_1", "t_2"]]
    r["manager_user_ids"] = ["u_mgr"]
    return fx


TERMS0 = {"policy_version": 0, "slot_minutes": 30, "reservation_duration_minutes": 90,
          "cancellation_cutoff_minutes": 120,
          "opening_hours": base_fixture()["restaurants"][0]["opening_hours"],
          "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}


def policy(**kw):
    p = {"effective_from": "2030-09-01", "slot_minutes": 15,
         "reservation_duration_minutes": 120, "cancellation_cutoff_minutes": 60,
         "opening_hours": [{"weekday": "thu", "opens": "17:00", "closes": "23:00"},
                           {"weekday": "sat", "opens": "12:00", "closes": "22:00"}],
         "capacities": {"t_1": 3, "t_2": 4, "t_3": 8}}
    p.update(kw)
    return p


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


def publish(api, tok, p=None, key=None, rid="r_anker"):
    return api.post(f"/restaurants/{rid}/policies", p or policy(), token=tok,
                    key=key or uuid.uuid4().hex)


def hist(api, tok, ref):
    r = api.get(f"/reservations/{ref}/history", token=tok)
    assert r.status_code == 200, r.text
    return r.json()


# --- explanations ------------------------------------------------------------------

@pytest.mark.parametrize("v", ["false", "1", "True", "TRUE", "", " true", "yes"])
def test_explain_bad_values(a3, v):
    assert_error(a3.avail(explain=v), 422, "validation_failed")


def test_without_explain_shape_unchanged(a3):
    for s in a3.avail().json()["slots"]:
        assert set(s) == SLOT_KEYS


def test_explain_shape_and_rules(a3, ada):
    a3.book(ada, table_id="t_2", local=f"{THU}T19:00")
    body = a3.avail(party_size=3, explain="true").json()
    assert len(body["slots"]) == 8
    by = {s["starts_at_local"][11:]: s for s in body["slots"]}
    s = by["19:30"]
    assert set(s) == SLOT_KEYS | {"explain"}
    assert [e["table_id"] for e in s["explain"]] == ["t_1", "t_2", "t_3"]
    for e in s["explain"]:
        assert set(e) == {"table_id", "policy_version", "available", "rules"}
        assert e["policy_version"] == 0
        assert [r["rule"] for r in e["rules"]] == ["capacity", "no_overlap"]
        for r in e["rules"]:
            assert set(r) == {"rule", "holds"}
    ex = {e["table_id"]: (e["available"], [r["holds"] for r in e["rules"]]) for e in s["explain"]}
    assert ex == {"t_1": (False, [False, True]), "t_2": (False, [True, False]),
                  "t_3": (True, [True, True])}
    assert s["available_table_ids"] == ["t_3"]
    for s in body["slots"]:
        assert [e["table_id"] for e in s["explain"] if e["available"]] == s["available_table_ids"]


def test_explain_both_false_and_empty_slot(a3, ada):
    a3.book(ada, table_id="t_1", local=f"{THU}T19:00")
    a3.book(ada, table_id="t_2", local=f"{THU}T19:00")
    a3.book(ada, table_id="t_3", local=f"{THU}T19:00")
    s = [x for x in a3.avail(party_size=3, explain="true").json()["slots"]
         if x["starts_at_local"].endswith("19:00")][0]
    assert s["available_table_ids"] == []
    t1 = s["explain"][0]
    assert t1["available"] is False and [r["holds"] for r in t1["rules"]] == [False, False]


def test_explain_closed_day(a3):
    assert a3.avail(date="2030-09-28", explain="true").json()["slots"] == []


def test_explain_policy_version(a3, mgr):
    assert publish(a3, mgr).status_code == 201
    body = a3.avail(party_size=3, explain="true").json()
    s = body["slots"][0]
    assert s["starts_at_local"] == f"{THU}T17:00"
    assert all(e["policy_version"] == 1 for e in s["explain"])
    assert s["explain"][0]["rules"][0]["holds"] is True   # t_1 capacity 3 under policy 1


# --- policies -------------------------------------------------------------------------

def test_publish_auth(a3, ada, mgr):
    assert_error(a3.post("/restaurants/r_anker/policies", policy(), key="x"), 401,
                 "unauthenticated")
    assert_error(publish(a3, ada), 403, "forbidden")
    assert_error(publish(a3, mgr, rid="nope"), 404, "not_found")
    assert_error(publish(a3, mgr, rid="r_hudson"), 403, "forbidden")
    assert_error(a3.post("/restaurants/r_anker/policies", policy(), token=mgr), 400,
                 "missing_idempotency_key")


def test_publish_shape_versions_and_list(a3, mgr):
    r = publish(a3, mgr, policy(extra="ignored"))
    assert r.status_code == 201, r.text
    b = r.json()
    assert set(b) == POLICY_KEYS
    assert b["policy_version"] == 1
    for k, v in policy().items():
        assert b[k] == v
    assert_error(publish(a3, mgr, policy(slot_minutes=0)), 422, "validation_failed")
    r2 = publish(a3, mgr, policy(effective_from="2030-01-01"), key="k2")
    assert r2.json()["policy_version"] == 2
    r2b = publish(a3, mgr, policy(effective_from="2030-01-01"), key="k2")
    assert r2b.status_code == 200 and r2b.json() == r2.json()
    assert_error(publish(a3, mgr, policy(effective_from="2030-01-02"), key="k2"), 409,
                 "idempotency_key_reuse")
    assert publish(a3, mgr).json()["policy_version"] == 3
    lst = a3.get("/restaurants/r_anker/policies")
    assert lst.status_code == 200
    assert [p["policy_version"] for p in lst.json()["policies"]] == [1, 2, 3]
    assert lst.json()["policies"][0] == b
    assert a3.get("/restaurants/r_hudson/policies").json() == {"policies": []}
    assert_error(a3.get("/restaurants/nope/policies"), 404, "not_found")
    # detail stays original
    d = a3.get("/restaurants/r_anker").json()
    assert d["slot_minutes"] == 30 and d["tables"][0]["capacity"] == 2


BAD = [
    ("effective_from", "2030-02-30"), ("effective_from", "2030-9-1"), ("effective_from", 5),
    ("effective_from", None), ("effective_from", " 2030-09-01"),
    ("slot_minutes", 0), ("slot_minutes", 1441), ("slot_minutes", True), ("slot_minutes", "30"),
    ("slot_minutes", 30.5), ("slot_minutes", None),
    ("reservation_duration_minutes", 0), ("reservation_duration_minutes", 1441),
    ("reservation_duration_minutes", False),
    ("cancellation_cutoff_minutes", -1), ("cancellation_cutoff_minutes", 10081),
    ("cancellation_cutoff_minutes", True), ("cancellation_cutoff_minutes", "60"),
    ("opening_hours", "thu"), ("opening_hours", None),
    ("opening_hours", [{"weekday": "xyz", "opens": "17:00", "closes": "23:00"}]),
    ("opening_hours", [{"weekday": "thu", "opens": "23:00", "closes": "17:00"}]),
    ("opening_hours", [{"weekday": "thu", "opens": "17:00", "closes": "17:00"}]),
    ("opening_hours", [{"weekday": "thu", "opens": "25:00", "closes": "26:00"}]),
    ("opening_hours", [{"weekday": "thu", "opens": "5:00", "closes": "23:00"}]),
    ("opening_hours", [{"weekday": "thu", "opens": "17:00", "closes": "23:00"},
                       {"weekday": "thu", "opens": "10:00", "closes": "12:00"}]),
    ("opening_hours", [{"weekday": "thu", "opens": "17:00"}]),
    ("capacities", {"t_1": 2, "t_2": 4}),
    ("capacities", {"t_1": 2, "t_2": 4, "t_3": 6, "t_9": 1}),
    ("capacities", {"t_1": 0, "t_2": 4, "t_3": 6}),
    ("capacities", {"t_1": 101, "t_2": 4, "t_3": 6}),
    ("capacities", {"t_1": True, "t_2": 4, "t_3": 6}),
    ("capacities", {"t_1": "2", "t_2": 4, "t_3": 6}),
    ("capacities", [2, 4, 6]), ("capacities", None),
]


@pytest.mark.parametrize("field,value", BAD, ids=[f"{f}={v!r}" for f, v in BAD])
def test_publish_invalid(a3, mgr, field, value):
    assert_error(publish(a3, mgr, policy(**{field: value})), 422, "validation_failed")
    assert a3.get("/restaurants/r_anker/policies").json() == {"policies": []}


@pytest.mark.parametrize("field", sorted(POLICY_KEYS - {"policy_version"}))
def test_publish_missing_field(a3, mgr, field):
    p = policy()
    del p[field]
    assert_error(publish(a3, mgr, p), 422, "validation_failed")


def test_publish_boundaries_ok(a3, mgr):
    assert publish(a3, mgr, policy(slot_minutes=1440, reservation_duration_minutes=1,
                                   cancellation_cutoff_minutes=0, opening_hours=[],
                                   capacities={"t_1": 1, "t_2": 100, "t_3": 1})).status_code == 201
    assert publish(a3, mgr, policy(cancellation_cutoff_minutes=10080)).status_code == 201


def test_failed_publish_key_reusable(a3, mgr):
    assert_error(publish(a3, mgr, policy(slot_minutes=0), key="pf"), 422)
    r = publish(a3, mgr, policy(), key="pf")
    assert r.status_code == 201 and r.json()["policy_version"] == 1


def test_policy_selection(a3, mgr, ada):
    publish(a3, mgr, policy(effective_from="2030-10-01", slot_minutes=60))       # v1
    publish(a3, mgr, policy(effective_from="2030-09-01", slot_minutes=20))       # v2
    publish(a3, mgr, policy(effective_from="2030-09-01", slot_minutes=45))       # v3 (tie wins)
    # 2030-09-26 -> greatest effective_from <= date is 09-01, tie -> v3
    s = a3.avail(date=THU, explain="true").json()["slots"]
    assert s[0]["explain"][0]["policy_version"] == 3
    assert [x["starts_at_local"][11:] for x in s[:3]] == ["17:00", "17:45", "18:30"]
    # before any effective date -> policy 0
    s = a3.avail(date="2030-08-29", explain="true").json()["slots"]
    assert s[0]["starts_at_local"].endswith("18:00") and s[0]["explain"][0]["policy_version"] == 0
    # on or after 10-01 -> v1
    s = a3.avail(date="2030-10-03", explain="true").json()["slots"]
    assert s[0]["explain"][0]["policy_version"] == 1
    # effective on its own date
    s = a3.avail(date="2030-10-03").json()["slots"]
    assert [x["starts_at_local"][11:] for x in s[:2]] == ["17:00", "18:00"]


def test_policy_drives_booking_rules(a3, mgr, ada):
    publish(a3, mgr)
    b = a3.book(ada, table_id="t_1", local=f"{THU}T17:15", party_size=3).json()
    assert b["ends_at"] == f"{THU}T19:15:00+02:00"
    assert b["accepted_terms"] == {k: v for k, v in {**policy(), "policy_version": 1}.items()
                                   if k != "effective_from"}
    assert_error(a3.book(ada, table_id="t_1", local=f"{THU}T22:00", party_size=1, expect=None),
                 422, "outside_opening_hours")
    assert_error(a3.book(ada, table_id="t_1", local=f"{THU}T19:10", party_size=1, expect=None),
                 422, "not_on_slot_grid")
    assert_error(a3.book(ada, table_id="t_1", local=f"{THU}T19:15", party_size=4, expect=None),
                 422, "party_exceeds_capacity")
    # friday is closed under policy 1
    assert_error(a3.book(ada, table_id="t_2", local=f"{FRI}T19:00", expect=None), 422,
                 "outside_opening_hours")
    # pair capacity is the policy's sum: 3 + 4
    r = a3.post("/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"],
                                  "starts_at_local": f"{THU}T20:00", "party_size": 7},
                token=ada, key="pair7")
    assert r.status_code == 201, r.text
    opts = a3.slot("r_anker", "2030-10-03", 7, "2030-10-03T17:00")["available_options"]
    assert {"table_ids": ["t_1", "t_2"], "capacity": 7} in opts


def test_publication_does_not_touch_existing(a3, mgr, ada):
    b = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    h = hist(a3, ada, b["reference"])
    publish(a3, mgr)
    assert a3.get(f"/reservations/{b['reference']}", token=ada).json() == b
    assert hist(a3, ada, b["reference"]) == h
    # still occupies its original 90-minute interval
    s = a3.slot("r_anker", THU, 1, f"{THU}T20:15")
    assert "t_2" not in s["available_table_ids"]
    s = a3.slot("r_anker", THU, 1, f"{THU}T20:30")
    assert "t_2" in s["available_table_ids"]


# --- terms, revision, history ---------------------------------------------------------

def test_create_terms_and_history(a3, ada):
    b = a3.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3).json()
    assert set(b) == RES_KEYS
    assert b["revision"] == 1 and b["accepted_terms"] == TERMS0
    h = hist(a3, ada, b["reference"])
    assert set(h) == {"reference", "entries"} and h["reference"] == b["reference"]
    (e,) = h["entries"]
    assert set(e) == {"seq", "at", "event", "changes", "revision", "accepted_terms"}
    assert e["seq"] == 1 and e["event"] == "created" and e["revision"] == 1
    assert TS_RE.match(e["at"])
    assert e["accepted_terms"] == TERMS0
    assert e["changes"] == [{"field": "table_id", "from": None, "to": "t_2"},
                            {"field": "starts_at_local", "from": None, "to": f"{THU}T19:00"},
                            {"field": "party_size", "from": None, "to": 3}]


def test_changed_noop_cancel_history(a3, ada):
    b = a3.book(ada, table_id="t_2", local=f"{THU}T19:00", party_size=3).json()
    ref = b["reference"]
    p = a3.patch(f"/reservations/{ref}", {"table_id": "t_3"}, token=ada).json()
    assert p["revision"] == 2
    noop = a3.patch(f"/reservations/{ref}", {"table_id": "t_3", "party_size": 3,
                                             "starts_at_local": f"{THU}T19:00"}, token=ada)
    assert noop.status_code == 200 and noop.json() == p
    p2 = a3.patch(f"/reservations/{ref}", {"party_size": 5, "starts_at_local": f"{THU}T20:00",
                                           "table_id": "t_3"}, token=ada).json()
    assert p2["revision"] == 3
    c = a3.post(f"/reservations/{ref}/cancel", {}, token=ada).json()
    assert c["revision"] == 4
    c2 = a3.post(f"/reservations/{ref}/cancel", {}, token=ada).json()
    assert c2 == c
    es = hist(a3, ada, ref)["entries"]
    assert [e["seq"] for e in es] == [1, 2, 3, 4]
    assert [e["event"] for e in es] == ["created", "changed", "changed", "cancelled"]
    assert [e["revision"] for e in es] == [1, 2, 3, 4]
    assert es[1]["changes"] == [{"field": "table_id", "from": "t_2", "to": "t_3"}]
    assert es[2]["changes"] == [
        {"field": "starts_at_local", "from": f"{THU}T19:00", "to": f"{THU}T20:00"},
        {"field": "party_size", "from": 3, "to": 5}]
    assert es[3]["changes"] == []
    ats = [e["at"] for e in es]
    from datetime import datetime
    parsed = [datetime.fromisoformat(a) for a in ats]
    assert parsed == sorted(parsed)


def test_failed_patch_records_nothing(a3, ada):
    b = a3.book(ada, table_id="t_2").json()
    a3.patch(f"/reservations/{b['reference']}", {"party_size": 99}, token=ada)
    assert len(hist(a3, ada, b["reference"])["entries"]) == 1
    assert a3.get(f"/reservations/{b['reference']}", token=ada).json()["revision"] == 1


def test_replay_records_nothing(a3, ada):
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{THU}T19:00",
            "party_size": 2}
    r1 = a3.post("/reservations", body, token=ada, key="rp").json()
    a3.patch(f"/reservations/{r1['reference']}", {"party_size": 3}, token=ada)
    r2 = a3.post("/reservations", body, token=ada, key="rp")
    assert r2.status_code == 200 and r2.json() == r1 and r2.json()["revision"] == 1
    assert len(hist(a3, ada, r1["reference"])["entries"]) == 2


def test_history_and_decision_access(a3, ada, bob):
    b = a3.book(ada).json()
    ref = b["reference"]
    for path in [f"/reservations/{ref}/history", f"/reservations/{ref}/decision"]:
        assert_error(a3.get(path, token=bob), 404, "not_found")
        assert_error(a3.get(path), 404, "not_found")
        assert_error(a3.get(path, headers={"Authorization": "Bearer junk"}), 404, "not_found")
    assert_error(a3.get("/reservations/NOPE0000/history", token=ada), 404, "not_found")
    d = a3.get(f"/reservations/{ref}/decision", token=ada)
    assert d.status_code == 200
    assert d.json() == {"reference": ref, "revision": 1, "accepted_terms": TERMS0}
    a3.post(f"/reservations/{ref}/cancel", {}, token=ada)
    assert a3.get(f"/reservations/{ref}/decision", token=ada).json()["revision"] == 2
    assert hist(a3, ada, ref)["entries"][-1]["event"] == "cancelled"


def test_amend_adopts_new_policy_and_keeps_old_entries(a3, mgr, ada):
    b = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    publish(a3, mgr, policy(effective_from="2030-10-01"))
    # amend into policy-1 date: 17:15 grid valid only under policy 1
    r = a3.patch(f"/reservations/{b['reference']}", {"starts_at_local": "2030-10-03T17:15"},
                 token=ada)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["revision"] == 2 and p["accepted_terms"]["policy_version"] == 1
    assert p["ends_at"] == "2030-10-03T19:15:00+02:00"
    es = hist(a3, ada, b["reference"])["entries"]
    assert es[0]["accepted_terms"] == TERMS0
    assert es[1]["accepted_terms"]["policy_version"] == 1
    # an amendment validated against the new date's policy fails cleanly
    assert_error(a3.patch(f"/reservations/{b['reference']}",
                          {"starts_at_local": "2030-10-04T19:00"}, token=ada), 422,
                 "outside_opening_hours")
    assert a3.get(f"/reservations/{b['reference']}", token=ada).json() == p


def test_noop_keeps_old_terms_after_publication(a3, mgr, ada):
    b = a3.book(ada, table_id="t_2", local=f"{THU}T19:00").json()
    publish(a3, mgr)    # effective 2030-09-01 covers THU, but no-op must not adopt it
    r = a3.patch(f"/reservations/{b['reference']}", {"party_size": b["party_size"]}, token=ada)
    assert r.status_code == 200 and r.json() == b


def test_cancel_uses_accepted_cutoff(a3, mgr, ada):
    # A booking accepted under cutoff 120 stays bound to it, a later policy with cutoff 0
    # does not relax it.
    b = a3.book(ada, table_id="t_2", local=f"{PAST_THU}T19:00").json()
    publish(a3, mgr, policy(effective_from="2025-01-01", cancellation_cutoff_minutes=0))
    assert_error(a3.post(f"/reservations/{b['reference']}/cancel", {}, token=ada), 409,
                 "cutoff_passed")


@pytest.mark.parametrize("v", [0, -1, True, "1", 1.5, None, [], {}])
def test_expected_revision_invalid(a3, ada, v):
    b = a3.book(ada).json()
    assert_error(a3.patch(f"/reservations/{b['reference']}",
                          {"party_size": 3, "expected_revision": v}, token=ada), 422,
                 "validation_failed")


def test_expected_revision_stale_and_ok(a3, ada):
    b = a3.book(ada).json()
    ref = b["reference"]
    assert_error(a3.patch(f"/reservations/{ref}", {"party_size": 3, "expected_revision": 2},
                          token=ada), 409, "stale_revision")
    r = a3.patch(f"/reservations/{ref}", {"party_size": 3, "expected_revision": 1}, token=ada)
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert_error(a3.patch(f"/reservations/{ref}", {"party_size": 4, "expected_revision": 1},
                          token=ada), 409, "stale_revision")
    # stale precedes validation and cutoff
    assert_error(a3.patch(f"/reservations/{ref}", {"party_size": 99, "expected_revision": 7},
                          token=ada), 409, "stale_revision")
    p = a3.book(ada, table_id="t_1", local=f"{PAST_THU}T19:00").json()
    assert_error(a3.patch(f"/reservations/{p['reference']}",
                          {"party_size": 1, "expected_revision": 5}, token=ada), 409,
                 "stale_revision")
    assert_error(a3.patch(f"/reservations/{ref}", {"party_size": 3, "expected_revision": 1},
                          token=bob_token(a3)), 404, "not_found")


def bob_token(api):
    return api.login("bob@example.com", "battery staple")


def test_concurrent_same_expected_revision(a3, ada):
    b = a3.book(ada, table_id="t_3", party_size=2).json()
    rs = parallel([lambda n=n: a3.patch(f"/reservations/{b['reference']}",
                                        {"party_size": n, "expected_revision": 1}, token=ada)
                   for n in range(3, 6) for _ in range(8)])
    ok = [r for r in rs if r.status_code == 200]
    assert len(ok) == 1, [r.status_code for r in rs]
    assert all(r.status_code in (200, 409) for r in rs)
    cur = a3.get(f"/reservations/{b['reference']}", token=ada).json()
    assert cur["revision"] == 2
    assert len(hist(a3, ada, b["reference"])["entries"]) == 2


def test_pair_history(a3, ada):
    r = a3.post("/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_2", "t_1"],
                                  "starts_at_local": f"{THU}T19:00", "party_size": 4},
                token=ada, key="ph")
    ref = r.json()["reference"]
    # reversed pair is not a change
    same = a3.patch(f"/reservations/{ref}", {"table_ids": ["t_2", "t_1"]}, token=ada)
    assert same.status_code == 200 and same.json()["revision"] == 1
    a3.patch(f"/reservations/{ref}", {"table_id": "t_3"}, token=ada)
    a3.patch(f"/reservations/{ref}", {"table_ids": ["t_2"]}, token=ada)
    a3.patch(f"/reservations/{ref}", {"table_ids": ["t_1", "t_2"]}, token=ada)
    es = hist(a3, ada, ref)["entries"]
    assert es[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]}
    assert es[1]["changes"] == [{"field": "table_ids", "from": ["t_1", "t_2"], "to": ["t_3"]}]
    assert es[2]["changes"] == [{"field": "table_id", "from": "t_3", "to": "t_2"}]
    assert es[3]["changes"] == [{"field": "table_ids", "from": ["t_2"], "to": ["t_1", "t_2"]}]
    assert len(es) == 4


def test_seeded_terms_and_history(a3):
    fx = fx3()
    fx["reservations"] = [{"id": "s1", "reference": "SEED01", "user_id": "u_ada",
                           "restaurant_id": "r_anker", "table_id": "t_2",
                           "starts_at_local": f"{THU}T19:00", "party_size": 2}]
    a3.reset(fx)
    tok = a3.login()
    b = a3.get("/reservations/SEED01", token=tok).json()
    assert b["revision"] == 1 and b["accepted_terms"] == TERMS0
    es = hist(a3, tok, "SEED01")["entries"]
    assert es[0]["event"] == "created" and es[0]["revision"] == 1


# --- moves under policies ---------------------------------------------------------------

def test_moves_revisions_and_history(a3, ada):
    a = a3.book(ada, table_id="t_1").json()
    b = a3.book(ada, table_id="t_2").json()
    c = a3.book(ada, table_id="t_3").json()
    r = a3.post("/reservation-moves", {"moves": [
        {"reference": a["reference"], "table_id": "t_2", "expected_revision": 1},
        {"reference": b["reference"], "table_id": "t_1"},
        {"reference": c["reference"]}]}, token=ada, key="mv")
    assert r.status_code == 201, r.text
    out = r.json()["reservations"]
    assert [x["revision"] for x in out] == [2, 2, 1]
    assert out[2] == c
    assert len(hist(a3, ada, a["reference"])["entries"]) == 2
    assert len(hist(a3, ada, c["reference"])["entries"]) == 1
    r2 = a3.post("/reservation-moves", {"moves": [
        {"reference": a["reference"], "table_id": "t_2", "expected_revision": 1},
        {"reference": b["reference"], "table_id": "t_1"},
        {"reference": c["reference"]}]}, token=ada, key="mv")
    assert r2.status_code == 200 and r2.json() == r.json()
    assert len(hist(a3, ada, a["reference"])["entries"]) == 2


def test_moves_stale_revision_atomic(a3, ada):
    a = a3.book(ada, table_id="t_1").json()
    b = a3.book(ada, table_id="t_2").json()
    r = a3.post("/reservation-moves", {"moves": [
        {"reference": a["reference"], "table_id": "t_3"},
        {"reference": b["reference"], "table_id": "t_1", "expected_revision": 9}]},
        token=ada, key="ms")
    assert_error(r, 409, "stale_revision")
    assert a3.get(f"/reservations/{a['reference']}", token=ada).json() == a
    r = a3.post("/reservation-moves", {"moves": [
        {"reference": a["reference"], "expected_revision": True}]}, token=ada, key="mt")
    assert_error(r, 422, "validation_failed")
