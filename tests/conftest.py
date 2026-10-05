"""Shared helpers for the black-box acceptance suites.

The service under test is reached at TK_BASE_URL (set by tests/run.sh).
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BASE_URL = os.environ.get("TK_BASE_URL", "http://127.0.0.1:8080")

TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
REF_RE = re.compile(r"^[A-Z0-9]{6,12}$")

# Calendar anchors (weekday noted).
THU = "2030-09-26"      # Thursday, Berlin +02:00
FRI = "2030-09-27"      # Friday
SAT = "2030-09-28"      # Saturday (closed at r_anker)
THU_WINTER = "2030-12-05"  # Thursday, Berlin +01:00
PAST_THU = "2026-01-01"  # Thursday in the past

PASSWORD = "correct horse"


def base_fixture() -> dict:
    return {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": PASSWORD,
             "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "battery staple",
             "display_name": "Bob"},
        ],
        "restaurants": [
            {
                "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [
                    {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                    {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
                    {"weekday": "sun", "opens": "00:00", "closes": "06:00"},
                ],
                "tables": [
                    {"id": "t_1", "label": "1", "capacity": 2},
                    {"id": "t_2", "label": "2", "capacity": 4},
                    {"id": "t_3", "label": "3", "capacity": 6},
                ],
            },
            {
                "id": "r_hudson", "name": "Hudson Grill", "timezone": "America/New_York",
                "slot_minutes": 15, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 60,
                "opening_hours": [
                    {"weekday": "sun", "opens": "00:00", "closes": "06:00"},
                    {"weekday": "mon", "opens": "17:00", "closes": "22:00"},
                    {"weekday": "thu", "opens": "17:00", "closes": "22:00"},
                ],
                "tables": [
                    {"id": "h_1", "label": "Window", "capacity": 4},
                    {"id": "h_2", "label": "Bar", "capacity": 2},
                ],
            },
        ],
        "reservations": [],
    }


class Api:
    def __init__(self, base_url: str = BASE_URL):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=15.0)

    # raw -------------------------------------------------------------------
    def req(self, method, path, *, token=None, key=None, json_body=None, content=None,
            headers=None, params=None):
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if key is not None:
            h["Idempotency-Key"] = key
        kw = {}
        if content is not None:
            kw["content"] = content
            h.setdefault("Content-Type", "application/json")
        elif json_body is not None:
            kw["content"] = json.dumps(json_body)
            h.setdefault("Content-Type", "application/json")
        r = self.client.request(method, path, headers=h, params=params, **kw)
        assert r.status_code < 500, f"{method} {path} -> {r.status_code} {r.text[:300]}"
        return r

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.req("POST", path, json_body=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.req("PATCH", path, json_body=body, **kw)

    # domain ---------------------------------------------------------------
    def reset(self, fixture=None):
        r = self.post("/_test/reset", fixture if fixture is not None else base_fixture())
        assert r.status_code == 204, r.text
        return r

    def login(self, email="ada@example.com", password=PASSWORD):
        r = self.post("/auth/login", {"email": email, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def signup(self, email=None, password="long enough pw", display_name="Zed"):
        email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
        r = self.post("/auth/signup", {"email": email, "password": password,
                                       "display_name": display_name})
        assert r.status_code == 201, r.text
        return r.json()

    def book(self, token, *, restaurant_id="r_anker", table_id="t_2", local=f"{THU}T19:00",
             party_size=2, key=None, expect=201, extra=None):
        body = {"restaurant_id": restaurant_id, "table_id": table_id,
                "starts_at_local": local, "party_size": party_size}
        if extra:
            body.update(extra)
        r = self.post("/reservations", body, token=token, key=key or uuid.uuid4().hex)
        if expect is not None:
            assert r.status_code == expect, r.text
        return r

    def avail(self, restaurant_id="r_anker", date=THU, party_size=2, **extra):
        params = {"restaurant_id": restaurant_id, "date": date, "party_size": str(party_size)}
        params.update(extra)
        return self.get("/availability", params=params)

    def slot(self, restaurant_id, date, party_size, local):
        r = self.avail(restaurant_id, date, party_size)
        assert r.status_code == 200, r.text
        for s in r.json()["slots"]:
            if s["starts_at_local"] == local:
                return s
        return None

    def export(self):
        r = self.get("/_test/export")
        assert r.status_code == 200, r.text
        return r.json()

    def import_(self, data):
        return self.post("/_test/import", data)


def assert_error(r, status, code=None):
    assert r.status_code == status, f"expected {status} {code}, got {r.status_code} {r.text}"
    body = r.json()
    assert set(body.keys()) == {"error"}, body
    assert isinstance(body["error"], dict), body
    assert set(body["error"].keys()) == {"code", "message"}, body
    assert isinstance(body["error"]["message"], str)
    if code is not None:
        assert body["error"]["code"] == code, body


def parallel(fns, workers=50):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(f) for f in fns]
        return [f.result() for f in futs]


@pytest.fixture
def api():
    a = Api()
    a.reset()
    yield a
    a.client.close()


@pytest.fixture
def ada(api):
    return api.login()


@pytest.fixture
def bob(api):
    return api.login("bob@example.com", "battery staple")


def fresh_fixture():
    return copy.deepcopy(base_fixture())
