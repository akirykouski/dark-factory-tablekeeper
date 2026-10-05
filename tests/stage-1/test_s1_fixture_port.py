"""S1-02, S1-19a: PORT handling and reset fixture validation."""
import copy
import os
import subprocess
import time

import httpx
import pytest

from conftest import THU, assert_error, base_fixture


def seeded(**kw):
    r = {"id": "res_s", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_anker",
         "table_id": "t_2", "starts_at_local": f"{THU}T19:00", "party_size": 2}
    r.update(kw)
    return r


def with_res(*rs):
    fx = copy.deepcopy(base_fixture())
    fx["reservations"] = list(rs)
    return fx


def mutate(path_fn):
    fx = copy.deepcopy(base_fixture())
    path_fn(fx)
    return fx


INVALID = {
    "ref_lower": with_res(seeded(reference="seed01")),
    "ref_short": with_res(seeded(reference="ABC12")),
    "ref_long": with_res(seeded(reference="A" * 13)),
    "ref_dash": with_res(seeded(reference="AB-123")),
    "ref_dup": with_res(seeded(), seeded(id="res_t", table_id="t_3")),
    "res_id_dup": with_res(seeded(), seeded(reference="SEED02", table_id="t_3")),
    "unknown_user": with_res(seeded(user_id="u_nobody")),
    "unknown_restaurant": with_res(seeded(restaurant_id="r_nope")),
    "unknown_table": with_res(seeded(table_id="t_9")),
    "bad_local": with_res(seeded(starts_at_local="2030-09-26 19:00")),
    "bad_party": with_res(seeded(party_size=0)),
    "seed_overlap": with_res(seeded(), seeded(id="res_t", reference="SEED02",
                                              starts_at_local=f"{THU}T19:30")),
    "bad_tz": mutate(lambda f: f["restaurants"][0].update(timezone="Mars/Olympus")),
    "slot_zero": mutate(lambda f: f["restaurants"][0].update(slot_minutes=0)),
    "dur_zero": mutate(lambda f: f["restaurants"][0].update(reservation_duration_minutes=0)),
    "cap_zero": mutate(lambda f: f["restaurants"][0]["tables"][0].update(capacity=0)),
    "bad_weekday": mutate(lambda f: f["restaurants"][0]["opening_hours"][0].update(weekday="thursday")),
    "bad_hhmm": mutate(lambda f: f["restaurants"][0]["opening_hours"][0].update(opens="6pm")),
    "closes_before": mutate(lambda f: f["restaurants"][0]["opening_hours"][0].update(closes="17:00")),
    "dup_user": mutate(lambda f: f["users"].append(dict(f["users"][0]))),
    "dup_restaurant": mutate(lambda f: f["restaurants"].append(copy.deepcopy(f["restaurants"][0]))),
    "dup_table": mutate(lambda f: f["restaurants"][0]["tables"].append(dict(f["restaurants"][0]["tables"][0]))),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_invalid_fixture_rejected_and_state_kept(api, ada, name):
    b = api.book(ada).json()
    r = api.post("/_test/reset", INVALID[name])
    assert_error(r, 422, "validation_failed")
    assert api.get(f"/reservations/{b['reference']}", token=ada).json() == b


def test_valid_seed_edges(api):
    api.reset(with_res(seeded(reference="ABCDEF"),
                       seeded(id="r2", reference="ABCDEFGHIJ12", starts_at_local=f"{THU}T20:30"),
                       seeded(id="r3", reference="AAAAAA", user_id="u_bob", table_id="t_1",
                              starts_at_local=f"{THU}T19:00")))
    tok = api.login()
    assert len(api.get("/reservations", token=tok).json()["reservations"]) == 2


IMAGE = os.environ.get("TK_IMAGE")


@pytest.mark.skipif(not IMAGE, reason="TK_IMAGE not set")
@pytest.mark.parametrize("env_port,probe", [("9123", 9123), (None, 8080)])
def test_listens_on_port_env(env_port, probe):
    args = ["docker", "run", "-d", "--rm", "-p", f"127.0.0.1::{probe}"]
    if env_port:
        args += ["-e", f"PORT={env_port}"]
    cid = subprocess.check_output(args + [IMAGE], text=True).strip()
    try:
        host = subprocess.check_output(["docker", "port", cid, str(probe)], text=True)
        hostport = host.strip().splitlines()[0].rsplit(":", 1)[1]
        ok = False
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{hostport}/health", timeout=1).status_code == 200:
                    ok = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        assert ok, f"service not listening on {probe} (PORT={env_port})"
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
