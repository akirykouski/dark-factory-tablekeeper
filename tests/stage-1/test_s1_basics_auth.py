"""S1-04..S1-33: runtime contract, conventions, errors, reset, authentication."""
import uuid

import pytest

from conftest import DETAIL_KEYS, PASSWORD, Api, assert_error, base_fixture, fresh_fixture

TYPES = {"string": "x", "number": 1, "boolean": True, "null": None, "array": [], "object": {}}


def test_health(api):
    r = api.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["content-type"].startswith("application/json")


def test_reset_is_204_and_repeatable(api):
    for _ in range(3):
        r = api.post("/_test/reset", base_fixture())
        assert r.status_code == 204
        assert r.content in (b"",)


def test_reset_clears_tokens_and_reservations(api, ada):
    res = api.book(ada).json()
    api.reset()
    assert_error(api.get("/reservations", token=ada), 401, "unauthenticated")
    tok = api.login()
    assert api.get("/reservations", token=tok).json() == {"reservations": []}
    assert_error(api.get(f"/reservations/{res['reference']}", token=tok), 404, "not_found")


def test_reset_clears_signed_up_users(api):
    u = api.signup(email="carol@example.com")
    api.reset()
    r = api.post("/auth/login", {"email": "carol@example.com", "password": "long enough pw"})
    assert_error(r, 401, "unauthenticated")


def test_reset_rejects_overlong_fixture_id(api):
    fx = fresh_fixture()
    fx["restaurants"][0]["id"] = "r" * 65
    r = api.post("/_test/reset", fx)
    assert_error(r, 422, "validation_failed")


def test_reset_accepts_64_char_ids(api):
    fx = fresh_fixture()
    fx["restaurants"][0]["id"] = "r" * 64
    fx["restaurants"][0]["tables"][0]["id"] = "t" * 64
    api.reset(fx)
    r = api.get("/restaurants/" + "r" * 64)
    assert r.status_code == 200
    assert r.json()["tables"][0]["id"] == "t" * 64


def test_restaurants_list_shape(api):
    r = api.get("/restaurants")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"restaurants": [
        {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin"},
        {"id": "r_hudson", "name": "Hudson Grill", "timezone": "America/New_York"},
    ]}


def test_restaurant_detail_shape(api):
    r = api.get("/restaurants/r_anker")
    assert r.status_code == 200
    body = r.json()
    fx = base_fixture()["restaurants"][0]
    assert set(body) == DETAIL_KEYS
    for k in fx:
        if k in body:
            assert body[k] == fx[k], k


def test_restaurant_detail_unknown_and_overlong(api):
    assert_error(api.get("/restaurants/nope"), 404, "not_found")
    assert_error(api.get("/restaurants/" + "x" * 65), 404, "not_found")


def test_public_endpoints_need_no_token(api):
    assert api.get("/restaurants").status_code == 200
    assert api.get("/restaurants/r_anker").status_code == 200
    assert api.avail().status_code == 200


# --- signup / login -----------------------------------------------------------

def test_signup_shape_and_token_works(api):
    r = api.post("/auth/signup", {"email": "zed@example.com", "password": "12345678",
                                  "display_name": "Zed", "extra": 1})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"user_id", "display_name", "token"}
    assert body["display_name"] == "Zed"
    assert isinstance(body["user_id"], str) and 0 < len(body["user_id"]) <= 64
    assert api.get("/reservations", token=body["token"]).json() == {"reservations": []}


def test_login_shape_and_seeded_user(api):
    r = api.post("/auth/login", {"email": "ada@example.com", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"user_id", "display_name", "token"}
    assert body == {"user_id": "u_ada", "display_name": "Ada", "token": body["token"]}


def test_login_after_signup(api):
    api.signup(email="lin@example.com", password="pw123456", display_name="Lin")
    r = api.post("/auth/login", {"email": "lin@example.com", "password": "pw123456"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Lin"


def test_multiple_tokens_all_valid(api):
    t1, t2 = api.login(), api.login()
    assert t1 != t2
    assert api.get("/reservations", token=t1).status_code == 200
    assert api.get("/reservations", token=t2).status_code == 200


def test_email_taken(api):
    r = api.post("/auth/signup", {"email": "ada@example.com", "password": "12345678",
                                  "display_name": "A2"})
    assert_error(r, 409, "email_taken")
    r = api.post("/auth/signup", {"email": "ADA@example.com", "password": "12345678",
                                  "display_name": "A2"})
    assert_error(r, 409, "email_taken")


def test_email_taken_does_not_change_password(api):
    api.post("/auth/signup", {"email": "ada@example.com", "password": "other password",
                              "display_name": "X"})
    api.login()  # old password still works


@pytest.mark.parametrize("pw,ok", [("1234567", False), ("12345678", True), ("", False),
                                   ("äöüßäöü", False), ("äöüßäöüß", True)])
def test_password_length(api, pw, ok):
    r = api.post("/auth/signup", {"email": f"{uuid.uuid4().hex[:8]}@ex.com", "password": pw,
                                  "display_name": "P"})
    if ok:
        assert r.status_code == 201, r.text
    else:
        assert_error(r, 422, "validation_failed")


@pytest.mark.parametrize("email", ["plain", "@example.com", "a@", "a@@b.com", "a@b@c.com",
                                   " a@b.com", "a@b.com ", "a@b.com\n", "a\t@b.com",
                                   "a b@c.com", ""])
def test_signup_bad_email(api, email):
    r = api.post("/auth/signup", {"email": email, "password": "12345678",
                                  "display_name": "E"})
    assert_error(r, 422, "validation_failed")


@pytest.mark.parametrize("field", ["email", "password", "display_name"])
@pytest.mark.parametrize("tname", ["number", "boolean", "null", "array", "object"])
def test_signup_type_matrix(api, field, tname):
    body = {"email": "t@example.com", "password": "12345678", "display_name": "T"}
    body[field] = TYPES[tname]
    assert_error(api.post("/auth/signup", body), 400, "malformed_request")


@pytest.mark.parametrize("field", ["email", "password", "display_name"])
def test_signup_missing_field(api, field):
    body = {"email": "t@example.com", "password": "12345678", "display_name": "T"}
    del body[field]
    assert_error(api.post("/auth/signup", body), 422, "validation_failed")


def test_signup_empty_display_name(api):
    r = api.post("/auth/signup", {"email": "t@example.com", "password": "12345678",
                                  "display_name": ""})
    assert_error(r, 422, "validation_failed")


@pytest.mark.parametrize("field", ["email", "password"])
@pytest.mark.parametrize("tname", ["number", "boolean", "null", "array", "object"])
def test_login_type_matrix(api, field, tname):
    body = {"email": "ada@example.com", "password": PASSWORD}
    body[field] = TYPES[tname]
    assert_error(api.post("/auth/login", body), 400, "malformed_request")


@pytest.mark.parametrize("body", [{"email": "ada@example.com", "password": "wrong pass"},
                                  {"email": "nobody@example.com", "password": PASSWORD}])
def test_login_wrong(api, body):
    assert_error(api.post("/auth/login", body), 401, "unauthenticated")


@pytest.mark.parametrize("raw", [b"{", b"not json", b"[]", b"\"str\"", b"12", b"null", b""])
def test_signup_malformed_body(api, raw):
    assert_error(api.req("POST", "/auth/signup", content=raw), 400, "malformed_request")


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer", "Bearer ", "Bearer nope",
                                    "bearer"])
def test_bad_bearer(api, header):
    h = {} if header is None else {"Authorization": header}
    assert_error(api.req("GET", "/reservations", headers=h), 401, "unauthenticated")


def test_protected_endpoints_require_token(api):
    assert_error(api.post("/reservations", {}, key="k"), 401, "unauthenticated")
    assert_error(api.get("/reservations/ABCDEF"), 401, "unauthenticated")
    assert_error(api.post("/reservations/ABCDEF/cancel", {}), 401, "unauthenticated")
    assert_error(api.patch("/reservations/ABCDEF", {}), 401, "unauthenticated")
    assert_error(api.post("/reservation-moves", {"moves": []}, key="k"), 401,
                 "unauthenticated")


def test_password_not_stored_plaintext(api):
    api.signup(email="sec@example.com", password="sup3r-secret-pw")
    import json
    dump = json.dumps(api.export())
    assert "sup3r-secret-pw" not in dump
    assert PASSWORD not in dump


def test_unknown_route_is_json_404(api):
    r = api.get("/definitely-not-here")
    assert r.status_code == 404
    assert_error(r, 404)
