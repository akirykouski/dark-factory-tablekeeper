"""Tablekeeper reservations API."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.requests import Request

class State:
    def __init__(self):
        self.users = {}
        self.tokens = {}
        self.restaurants = {}
        self.reservations = {}
        self.idempotency = {}
        self.occupancy = {}
        self.policies = {}
        self.series = {}
        self.closures = {}  # (rid, table_id) -> [(from_utc, to_utc)]
        self.replans = {}  # plan_id -> plan object
        self.restaurant_revisions = {}  # rid -> revision number
        self.lock = asyncio.Lock()

state = State()


def error_response(status: int, code: str) -> JSONResponse:
    """Standard error response."""
    return JSONResponse(
        {"error": {"code": code, "message": code.replace("_", " ").title()}},
        status_code=status
    )


def get_now_utc():
    return datetime.now(timezone.utc)


def format_rfc3339(dt: datetime) -> str:
    """RFC 3339 with explicit offset, second precision."""
    return dt.isoformat(timespec="seconds")


def parse_rfc3339(s: str) -> datetime:
    """Parse RFC 3339 timestamp."""
    return datetime.fromisoformat(s)


def hash_password(password: str) -> tuple[str, str]:
    """Hash password with random salt."""
    salt = secrets.token_hex(16)
    hash_obj = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=16384,
        r=8,
        p=1,
        dklen=32
    )
    return hash_obj.hex(), salt


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    """Verify password against hash."""
    hash_obj = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt_hex),
        n=16384,
        r=8,
        p=1,
        dklen=32
    )
    return hash_obj.hex() == hash_hex


def generate_reference() -> str:
    """Generate unique reference."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))


def validate_email(email: str) -> bool:
    """Validate email: local@domain, no whitespace anywhere."""
    if not isinstance(email, str):
        return False
    if email != email.strip():
        return False
    if not email or ' ' in email or '\t' in email or '\n' in email or '\r' in email:
        return False
    if email.count('@') != 1:
        return False
    local, domain = email.split('@')
    return bool(local and domain)


def validate_password(password: str) -> bool:
    """Password >= 8 characters."""
    return isinstance(password, str) and len(password) >= 8


def validate_display_name(name: str) -> bool:
    """Non-empty string."""
    return isinstance(name, str) and bool(name)


def from_local_time(local_str: str, tz_name: str) -> Optional[datetime]:
    """Parse YYYY-MM-DDTHH:MM and convert to UTC. None if invalid or nonexistent."""
    if not isinstance(local_str, str):
        return None

    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d', local_str):
        return None

    try:
        date_str, time_str = local_str.split('T')
        year, month, day = map(int, date_str.split('-'))
        hour, minute = map(int, time_str.split(':'))

        # Reject invalid time components (24:00, 19:60)
        if hour > 23 or minute > 59:
            return None

        # Validate that it's a real calendar date
        datetime(year, month, day, hour, minute)

        tz = ZoneInfo(tz_name)
        naive = datetime(year, month, day, hour, minute)
        local_dt = naive.replace(fold=0, tzinfo=tz)

        # Check if nonexistent (spring forward)
        utc_dt = local_dt.astimezone(timezone.utc)
        back = utc_dt.astimezone(tz)

        if back.replace(tzinfo=None) != naive:
            return None

        return utc_dt
    except (ValueError, KeyError):
        return None


def get_token_from_header(request: Request) -> Optional[str]:
    """Extract bearer token. None if missing/malformed."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return token if token else None


async def require_auth(request: Request) -> tuple[Optional[str], Optional[JSONResponse]]:
    """Check auth, return (user_id, error)."""
    token = get_token_from_header(request)
    if not token or token not in state.tokens:
        return None, error_response(401, "unauthenticated")
    return state.tokens[token], None


def format_reservation_response(res: dict, tz_name: str) -> dict:
    """Format reservation with local timezone for starts_at/ends_at."""
    tz = ZoneInfo(tz_name)
    starts_utc = parse_rfc3339(res["starts_at"])
    ends_utc = parse_rfc3339(res["ends_at"])

    table_ids = res.get("table_ids", [res["table_id"]] if "table_id" in res else [])
    resp = {
        "reservation_id": res["reservation_id"],
        "reference": res["reference"],
        "restaurant_id": res["restaurant_id"],
        "table_ids": list(table_ids),
        "party_size": res["party_size"],
        "status": res["status"],
        "starts_at_local": res["starts_at_local"],
        "starts_at": format_rfc3339(starts_utc.astimezone(tz)),
        "ends_at": format_rfc3339(ends_utc.astimezone(tz)),
        "created_at": res["created_at"],
        "revision": res["revision"],
        "accepted_terms": copy.deepcopy(res["terms"]),
    }
    if len(table_ids) == 1:
        resp["table_id"] = table_ids[0]
    return resp


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TERM_FIELDS = ("slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes",
               "opening_hours", "capacities")
DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
LOCAL_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}')


def policy0_terms(r: dict) -> dict:
    return {
        "policy_version": 0,
        "slot_minutes": r["slot_minutes"],
        "reservation_duration_minutes": r["reservation_duration_minutes"],
        "cancellation_cutoff_minutes": r.get("cancellation_cutoff_minutes", 0),
        "opening_hours": copy.deepcopy(r["opening_hours"]),
        "capacities": {t["id"]: t["capacity"] for t in r["tables"]},
    }


def select_terms(restaurant: dict, date: str) -> dict:
    """Terms of the policy applying to a local date (policy 0 when none is effective)."""
    best = None
    for p in state.policies.get(restaurant["id"], []):
        if p["effective_from"] <= date and (
                best is None or (p["effective_from"], p["policy_version"])
                > (best["effective_from"], best["policy_version"])):
            best = p
    if best is None:
        return policy0_terms(restaurant)
    terms = {"policy_version": best["policy_version"]}
    for k in TERM_FIELDS:
        terms[k] = copy.deepcopy(best[k])
    return terms


def is_occupied(rid: str, table_id: str, s: datetime, e: datetime) -> bool:
    for s1, e1 in state.occupancy.get((rid, table_id), []):
        if s < e1 and e > s1:
            return True
    return False


def is_closed(rid: str, table_id: str, s: datetime, e: datetime) -> bool:
    for s1, e1 in state.closures.get((rid, table_id), []):
        if s < e1 and e > s1:
            return True
    return False


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def valid_hhmm(v) -> Optional[int]:
    if not isinstance(v, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', v):
        return None
    h, m = int(v[:2]), int(v[3:])
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def valid_date(v) -> bool:
    if not isinstance(v, str) or not DATE_RE.fullmatch(v):
        return False
    try:
        datetime(int(v[:4]), int(v[5:7]), int(v[8:]))
    except ValueError:
        return False
    return True


def validate_policy(body: dict, restaurant: dict) -> Optional[dict]:
    """Complete-policy validation. Returns the normalized policy (without version) or None."""
    if not valid_date(body.get("effective_from")):
        return None
    out = {"effective_from": body["effective_from"]}
    for f, lo, hi in (("slot_minutes", 1, 1440), ("reservation_duration_minutes", 1, 1440),
                      ("cancellation_cutoff_minutes", 0, 10080)):
        v = body.get(f)
        if not is_int(v) or v < lo or v > hi:
            return None
        out[f] = v
    oh = body.get("opening_hours")
    if not isinstance(oh, list):
        return None
    hours, seen = [], set()
    for e in oh:
        if not isinstance(e, dict) or e.get("weekday") not in WEEKDAYS or e["weekday"] in seen:
            return None
        o, c = valid_hhmm(e.get("opens")), valid_hhmm(e.get("closes"))
        if o is None or c is None or c <= o:
            return None
        seen.add(e["weekday"])
        hours.append({"weekday": e["weekday"], "opens": e["opens"], "closes": e["closes"]})
    out["opening_hours"] = hours
    caps = body.get("capacities")
    ids = [t["id"] for t in restaurant["tables"]]
    if not isinstance(caps, dict) or set(caps.keys()) != set(ids):
        return None
    for tid in ids:
        if not is_int(caps[tid]) or caps[tid] < 1 or caps[tid] > 100:
            return None
    out["capacities"] = {tid: caps[tid] for tid in ids}
    return out


def terms_of_reservation(res: dict) -> dict:
    return res["terms"]


def add_history(res: dict, tz, event: str, changes: list, at: Optional[datetime] = None, plan_id: Optional[str] = None) -> None:
    hist = res["history"]
    moment = at or get_now_utc()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    at_dt = moment.astimezone(tz).replace(microsecond=0)
    if hist:
        prev = parse_rfc3339(hist[-1]["at"])
        if at_dt < prev:
            at_dt = prev.astimezone(tz)
    entry = {
        "seq": len(hist) + 1,
        "at": format_rfc3339(at_dt),
        "event": event,
        "changes": changes,
        "revision": res["revision"],
        "accepted_terms": copy.deepcopy(res["terms"]),
    }
    if plan_id:
        entry["plan_id"] = plan_id
    hist.append(entry)


def created_changes(table_ids: list, local: str, party: int) -> list:
    if len(table_ids) == 1:
        first = {"field": "table_id", "from": None, "to": table_ids[0]}
    else:
        first = {"field": "table_ids", "from": None, "to": list(table_ids)}
    return [first,
            {"field": "starts_at_local", "from": None, "to": local},
            {"field": "party_size", "from": None, "to": party}]


def diff_changes(old: tuple, new: tuple) -> list:
    ot, ol, op = old
    nt, nl, np_ = new
    ch = []
    if list(ot) != list(nt):
        if len(ot) == 1 and len(nt) == 1:
            ch.append({"field": "table_id", "from": ot[0], "to": nt[0]})
        else:
            ch.append({"field": "table_ids", "from": list(ot), "to": list(nt)})
    if ol != nl:
        ch.append({"field": "starts_at_local", "from": ol, "to": nl})
    if op != np_:
        ch.append({"field": "party_size", "from": op, "to": np_})
    return ch


def init_synthesized(rec: dict, restaurant: dict) -> None:
    """Give a bare reservation policy-0 terms, revision and a synthesized history."""
    tz = ZoneInfo(restaurant["timezone"])
    rec["terms"] = policy0_terms(restaurant)
    rec["revision"] = 1
    rec["history"] = []
    rec.setdefault("series_id", None)
    created = parse_rfc3339(rec["created_at"])
    add_history(rec, tz, "created",
                created_changes(rec["table_ids"], rec["starts_at_local"], rec["party_size"]), at=created)
    if rec["status"] == "cancelled":
        rec["revision"] = 2
        add_history(rec, tz, "cancelled", [], at=created)


def evaluate(restaurant: dict, table_ids: list, local: str, party: int):
    """Field and rule validation against the policy of the local start date.
    Returns (error|None, starts_utc, ends_utc, terms)."""
    def fail(status, code):
        return error_response(status, code), None, None, None

    if party < 1 or not LOCAL_RE.fullmatch(local):
        return fail(422, "validation_failed")
    try:
        year, month, day = int(local[:4]), int(local[5:7]), int(local[8:10])
        hour, minute = int(local[11:13]), int(local[14:16])
        if hour > 23 or minute > 59:
            return fail(422, "validation_failed")
        datetime(year, month, day, hour, minute)
    except ValueError:
        return fail(422, "validation_failed")

    for table_id in table_ids:
        if not any(t["id"] == table_id for t in restaurant["tables"]):
            return fail(404, "not_found")

    terms = select_terms(restaurant, local[:10])
    tz = ZoneInfo(restaurant["timezone"])
    starts_utc = from_local_time(local, restaurant["timezone"])
    if starts_utc is None:
        return fail(422, "invalid_local_time")

    oh = next((o for o in terms["opening_hours"]
               if o["weekday"] == WEEKDAYS[datetime(year, month, day).weekday()]), None)
    if oh is None:
        return fail(422, "outside_opening_hours")

    local_dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    oh_h, oh_m = map(int, oh["opens"].split(':'))
    ch_h, ch_m = map(int, oh["closes"].split(':'))
    opens_dt = datetime(year, month, day, oh_h, oh_m, tzinfo=tz)
    closes_dt = datetime(year, month, day, ch_h, ch_m, tzinfo=tz)
    if local_dt < opens_dt:
        return fail(422, "outside_opening_hours")

    ends_utc = starts_utc + timedelta(minutes=terms["reservation_duration_minutes"])
    if ends_utc.astimezone(tz) > closes_dt:
        return fail(422, "outside_opening_hours")

    if int((local_dt - opens_dt).total_seconds() / 60) % terms["slot_minutes"] != 0:
        return fail(422, "not_on_slot_grid")

    capacity = sum(terms["capacities"].get(t, 0) for t in table_ids)
    if party > capacity:
        return fail(422, "party_exceeds_capacity")
    return None, starts_utc, ends_utc, terms


def cutoff_passed(res: dict) -> bool:
    cutoff = timedelta(minutes=res["terms"]["cancellation_cutoff_minutes"])
    return get_now_utc() >= parse_rfc3339(res["starts_at"]) - cutoff


def owned_reservation(user_id: str, reference: str) -> Optional[dict]:
    res = state.reservations.get(reference)
    if res is None or res["user_id"] != user_id:
        return None
    return res


def valid_expected_revision(v) -> bool:
    return is_int(v) and v > 0


# Endpoints ====================================================================

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def auth_signup(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    for field in ["email", "password", "display_name"]:
        if field not in body:
            return error_response(422, "validation_failed")

    email = body["email"]
    password = body["password"]
    display_name = body["display_name"]

    if not isinstance(email, str) or not isinstance(password, str) or not isinstance(display_name, str):
        return error_response(400, "malformed_request")

    if not validate_email(email):
        return error_response(422, "validation_failed")
    if not validate_password(password):
        return error_response(422, "validation_failed")
    if not validate_display_name(display_name):
        return error_response(422, "validation_failed")

    email_lower = email.lower()

    for user in state.users.values():
        if user["email_lower"] == email_lower:
            return error_response(409, "email_taken")

    user_id = f"u_{uuid.uuid4().hex[:16]}"
    password_hash, password_salt = hash_password(password)

    state.users[user_id] = {
        "id": user_id,
        "email_lower": email_lower,
        "password_hash": password_hash,
        "password_salt": password_salt,
        "display_name": display_name,
    }

    token = secrets.token_urlsafe(32)
    state.tokens[token] = user_id

    return JSONResponse(
        {"user_id": user_id, "display_name": display_name, "token": token},
        status_code=201
    )


async def auth_login(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    email = body.get("email")
    password = body.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        return error_response(400, "malformed_request")

    email_lower = email.lower()

    user = None
    for u in state.users.values():
        if u["email_lower"] == email_lower:
            user = u
            break

    if not user or not verify_password(password, user["password_hash"], user["password_salt"]):
        return error_response(401, "unauthenticated")

    token = secrets.token_urlsafe(32)
    state.tokens[token] = user["id"]

    return JSONResponse(
        {"user_id": user["id"], "display_name": user["display_name"], "token": token},
        status_code=200
    )


async def get_restaurants(request: Request) -> JSONResponse:
    restaurants = []
    for rid in state.restaurants.keys():
        r = state.restaurants[rid]
        restaurants.append({
            "id": r["id"],
            "name": r["name"],
            "timezone": r["timezone"],
        })
    return JSONResponse({"restaurants": restaurants})


async def get_restaurant(request: Request) -> JSONResponse:
    rid = request.path_params.get("id")

    if not rid or len(rid) > 64:
        return error_response(404, "not_found")

    if rid not in state.restaurants:
        return error_response(404, "not_found")

    r = state.restaurants[rid]
    resp = dict(r)
    if "combinable" not in resp:
        resp["combinable"] = []
    resp.setdefault("manager_user_ids", [])
    return JSONResponse(resp)


def build_available_options(restaurant: dict, terms: dict, slot_utc: datetime, slot_end_utc: datetime, party_size: int) -> list:
    """Build available_options: singles first, then pairs."""
    rid = restaurant["id"]
    caps = terms["capacities"]
    options = []
    for table in restaurant["tables"]:
        cap = caps[table["id"]]
        if cap >= party_size and not is_occupied(rid, table["id"], slot_utc, slot_end_utc):
            options.append({"table_ids": [table["id"]], "capacity": cap})
    for pair in restaurant.get("combinable", []):
        t1_id, t2_id = pair
        if t1_id not in caps or t2_id not in caps:
            continue
        capacity = caps[t1_id] + caps[t2_id]
        if capacity >= party_size and not is_occupied(rid, t1_id, slot_utc, slot_end_utc) \
                and not is_occupied(rid, t2_id, slot_utc, slot_end_utc):
            options.append({"table_ids": list(pair), "capacity": capacity})
    return options


async def get_availability(request: Request) -> JSONResponse:
    restaurant_id = request.query_params.get("restaurant_id")
    date = request.query_params.get("date")
    party_size = request.query_params.get("party_size")

    if not restaurant_id or not date or not party_size:
        return error_response(422, "validation_failed")

    if not re.fullmatch(r'[0-9]+', party_size):
        return error_response(422, "validation_failed")

    party_size_int = int(party_size)
    if party_size_int < 1:
        return error_response(422, "validation_failed")

    if not valid_date(date):
        return error_response(422, "validation_failed")
    year, month, day = map(int, date.split('-'))

    explain = False
    if "explain" in request.query_params:
        if any(v != "true" for v in request.query_params.getlist("explain")):
            return error_response(422, "validation_failed")
        explain = True

    if restaurant_id not in state.restaurants:
        return error_response(404, "not_found")

    restaurant = state.restaurants[restaurant_id]
    tz_name = restaurant["timezone"]
    terms = select_terms(restaurant, date)
    slot_minutes = terms["slot_minutes"]
    duration_minutes = terms["reservation_duration_minutes"]
    caps = terms["capacities"]

    weekday = WEEKDAYS[datetime(year, month, day).weekday()]
    oh = next((o for o in terms["opening_hours"] if o["weekday"] == weekday), None)

    slots = []
    if oh:
        oh_h, oh_m = map(int, oh["opens"].split(':'))
        ch_h, ch_m = map(int, oh["closes"].split(':'))

        tz = ZoneInfo(tz_name)
        current_min = oh_h * 60 + oh_m
        closes_total_min = ch_h * 60 + ch_m

        while current_min < closes_total_min:
            slot_h = current_min // 60
            slot_m = current_min % 60

            if current_min + duration_minutes > closes_total_min:
                break

            naive = datetime(year, month, day, slot_h, slot_m)
            test = naive.replace(fold=0, tzinfo=tz)
            if test.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:
                current_min += slot_minutes
                continue

            slot_utc = test.astimezone(timezone.utc)
            slot_end_utc = slot_utc + timedelta(minutes=duration_minutes)

            available = []
            explained = []
            for table in restaurant["tables"]:
                cap_ok = caps[table["id"]] >= party_size_int
                occupied = is_occupied(restaurant_id, table["id"], slot_utc, slot_end_utc)
                closed = is_closed(restaurant_id, table["id"], slot_utc, slot_end_utc)
                free = not occupied and not closed
                if cap_ok and free:
                    available.append(table["id"])
                if explain:
                    explained.append({
                        "table_id": table["id"],
                        "policy_version": terms["policy_version"],
                        "available": cap_ok and free,
                        "rules": [{"rule": "capacity", "holds": cap_ok},
                                  {"rule": "no_overlap", "holds": not occupied}],
                    })

            slot = {
                "starts_at_local": f"{date}T{slot_h:02d}:{slot_m:02d}",
                "starts_at": format_rfc3339(slot_utc.astimezone(tz)),
                "available_table_ids": available,
                "available_options": build_available_options(
                    restaurant, terms, slot_utc, slot_end_utc, party_size_int),
            }
            if explain:
                slot["explain"] = explained
            slots.append(slot)

            current_min += slot_minutes

    return JSONResponse({
        "restaurant_id": restaurant_id,
        "date": date,
        "timezone": tz_name,
        "slots": slots,
    })


def validate_table_ids(body: dict, restaurant: dict) -> tuple[Optional[tuple[int, str]], Optional[list]]:
    """Validate table_id or table_ids from request body. Returns ((status, error_code)|None, table_ids|None)."""
    has_table_id = "table_id" in body
    has_table_ids = "table_ids" in body

    if has_table_id and has_table_ids:
        return (422, "validation_failed"), None

    if not has_table_id and not has_table_ids:
        return (422, "validation_failed"), None

    if has_table_id:
        table_id = body["table_id"]
        if not isinstance(table_id, str):
            return (400, "malformed_request"), None
        table_ids = [table_id]
    else:
        table_ids = body["table_ids"]
        if not isinstance(table_ids, list):
            return (400, "malformed_request"), None
        if len(table_ids) == 0:
            return (422, "validation_failed"), None
        for tid in table_ids:
            if not isinstance(tid, str):
                return (400, "malformed_request"), None

    # Check for duplicates
    if len(set(table_ids)) != len(table_ids):
        return (422, "validation_failed"), None

    # Check size
    if len(table_ids) > 2:
        return (422, "combination_not_allowed"), None

    # Check tables exist and are in same restaurant (before pair check)
    for tid in table_ids:
        found = False
        for t in restaurant["tables"]:
            if t["id"] == tid:
                found = True
                break
        if not found:
            return (404, "not_found"), None

    # Check pair is declared and normalize to declared order
    if len(table_ids) == 2:
        t1, t2 = table_ids
        combinable = restaurant.get("combinable", [])
        found_pair = None
        for pair in combinable:
            if set(pair) == {t1, t2}:
                found_pair = pair
                break
        if not found_pair:
            return (422, "combination_not_allowed"), None
        table_ids = list(found_pair)

    return None, table_ids


async def create_reservation(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")

    if len(key) > 255:
        return error_response(422, "validation_failed")

    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    async with state.lock:
        idempotency_key = (user_id, "/reservations", key)
        if idempotency_key in state.idempotency:
            recorded = state.idempotency[idempotency_key]
            current_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
            if current_json == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            else:
                return error_response(409, "idempotency_key_reuse")

        if "restaurant_id" not in body or "starts_at_local" not in body or "party_size" not in body:
            return error_response(422, "validation_failed")

        restaurant_id = body["restaurant_id"]
        starts_at_local = body["starts_at_local"]
        party_size = body["party_size"]

        if not isinstance(restaurant_id, str):
            return error_response(400, "malformed_request")
        if not isinstance(starts_at_local, str):
            return error_response(400, "malformed_request")
        if not is_int(party_size) or party_size < 1:
            return error_response(422, "validation_failed")

        if not LOCAL_RE.fullmatch(starts_at_local):
            return error_response(422, "validation_failed")

        if restaurant_id not in state.restaurants:
            return error_response(404, "not_found")

        restaurant = state.restaurants[restaurant_id]

        err, table_ids = validate_table_ids(body, restaurant)
        if err:
            status, code = err
            return error_response(status, code)

        err, starts_utc, ends_utc, terms = evaluate(restaurant, table_ids, starts_at_local, party_size)
        if err:
            return err

        for table_id in table_ids:
            if is_occupied(restaurant_id, table_id, starts_utc, ends_utc) or is_closed(restaurant_id, table_id, starts_utc, ends_utc):
                return error_response(409, "table_unavailable")

        reservation_id = f"res_{uuid.uuid4().hex[:16]}"
        reference = generate_reference()
        while reference in state.reservations:
            reference = generate_reference()

        now_utc = get_now_utc()
        tz = ZoneInfo(restaurant["timezone"])

        reservation = {
            "reservation_id": reservation_id,
            "reference": reference,
            "restaurant_id": restaurant_id,
            "table_ids": table_ids,
            "party_size": party_size,
            "status": "confirmed",
            "starts_at_local": starts_at_local,
            "starts_at": format_rfc3339(starts_utc),
            "ends_at": format_rfc3339(ends_utc),
            "created_at": format_rfc3339(now_utc),
            "user_id": user_id,
            "revision": 1,
            "terms": terms,
            "history": [],
            "series_id": None,
        }
        add_history(reservation, tz, "created", created_changes(table_ids, starts_at_local, party_size), at=now_utc)

        state.reservations[reference] = reservation
        for table_id in table_ids:
            state.occupancy.setdefault((restaurant_id, table_id), []).append((starts_utc, ends_utc))

        # Increment restaurant revision
        state.restaurant_revisions[restaurant_id] = state.restaurant_revisions.get(restaurant_id, 0) + 1

        response_body = format_reservation_response(reservation, restaurant["timezone"])
        state.idempotency[idempotency_key] = {
            "method": "POST",
            "path": "/reservations",
            "body_json": json.dumps(body, sort_keys=True, separators=(',', ':')),
            "response": response_body,
        }

        return JSONResponse(response_body, status_code=201)


async def list_reservations(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    reservations = []
    for ref, res in state.reservations.items():
        if res["user_id"] == user_id:
            tz_name = state.restaurants[res["restaurant_id"]]["timezone"]
            reservations.append(format_reservation_response(res, tz_name))

    def sort_key(r):
        starts = parse_rfc3339(r["starts_at"])
        created = parse_rfc3339(r["created_at"])
        return (-starts.timestamp(), -created.timestamp(), r["reference"])

    reservations.sort(key=sort_key)

    return JSONResponse({"reservations": reservations})


async def get_reservation(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    reference = request.path_params.get("reference", "")

    if reference not in state.reservations:
        return error_response(404, "not_found")

    res = state.reservations[reference]
    if res["user_id"] != user_id:
        return error_response(404, "not_found")

    tz_name = state.restaurants[res["restaurant_id"]]["timezone"]
    return JSONResponse(format_reservation_response(res, tz_name))


async def cancel_reservation(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    async with state.lock:
        res = owned_reservation(user_id, request.path_params.get("reference", ""))
        if res is None:
            return error_response(404, "not_found")

        restaurant = state.restaurants[res["restaurant_id"]]
        tz_name = restaurant["timezone"]

        if res["status"] == "cancelled":
            # Repeated cancel does nothing
            if res.get("series_id"):
                series_obj = state.series.get(res["series_id"])
                if series_obj:
                    for member in series_obj["members"]:
                        if member["reference"] == res["reference"]:
                            # Series revision was already incremented, but repeated cancel should not increment again
                            # For compatibility, we still return the response
                            break
            return JSONResponse(format_reservation_response(res, tz_name))

        if cutoff_passed(res):
            return error_response(409, "cutoff_passed")

        res["status"] = "cancelled"
        res["revision"] += 1
        add_history(res, ZoneInfo(tz_name), "cancelled", [])

        # Increment series revision but keep exception flag unchanged
        if res.get("series_id"):
            series_obj = state.series.get(res["series_id"])
            if series_obj:
                series_obj["revision"] += 1

        starts = parse_rfc3339(res["starts_at"])
        ends = parse_rfc3339(res["ends_at"])
        for table_id in res["table_ids"]:
            key = (res["restaurant_id"], table_id)
            if key in state.occupancy:
                state.occupancy[key] = [(s, e) for s, e in state.occupancy[key] if not (s == starts and e == ends)]

        # Increment restaurant revision for cancellation (not a repeated one)
        state.restaurant_revisions[res["restaurant_id"]] = state.restaurant_revisions.get(res["restaurant_id"], 0) + 1

        return JSONResponse(format_reservation_response(res, tz_name))


async def patch_reservation(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return error_response(400, "malformed_request")
    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    if "table_id" in body and not isinstance(body["table_id"], str):
        return error_response(400, "malformed_request")
    if "table_ids" in body and (not isinstance(body["table_ids"], list)
                                or not all(isinstance(t, str) for t in body["table_ids"])):
        return error_response(400, "malformed_request")
    if "starts_at_local" in body and not isinstance(body["starts_at_local"], str):
        return error_response(400, "malformed_request")

    async with state.lock:
        res = owned_reservation(user_id, request.path_params.get("reference", ""))
        if res is None:
            return error_response(404, "not_found")

        if "expected_revision" in body:
            er = body["expected_revision"]
            if not valid_expected_revision(er):
                return error_response(422, "validation_failed")
            if er != res["revision"]:
                return error_response(409, "stale_revision")

        if res["status"] == "cancelled":
            return error_response(409, "reservation_cancelled")
        if cutoff_passed(res):
            return error_response(409, "cutoff_passed")

        restaurant = state.restaurants[res["restaurant_id"]]
        tz_name = restaurant["timezone"]
        party_size = body.get("party_size", res["party_size"])
        if not is_int(party_size) or party_size < 1:
            return error_response(422, "validation_failed")
        starts_at_local = body.get("starts_at_local", res["starts_at_local"])
        if not LOCAL_RE.fullmatch(starts_at_local):
            return error_response(422, "validation_failed")

        table_ids = list(res["table_ids"])
        if "table_id" in body or "table_ids" in body:
            err, table_ids = validate_table_ids(body, restaurant)
            if err:
                return error_response(*err)

        old = (list(res["table_ids"]), res["starts_at_local"], res["party_size"])
        new = (table_ids, starts_at_local, party_size)
        if old == new:
            return JSONResponse(format_reservation_response(res, tz_name))

        err, starts_utc, ends_utc, terms = evaluate(restaurant, table_ids, starts_at_local, party_size)
        if err:
            return err

        rid = restaurant["id"]
        old_starts = parse_rfc3339(res["starts_at"])
        old_ends = parse_rfc3339(res["ends_at"])
        for table_id in table_ids:
            for s, e in state.occupancy.get((rid, table_id), []):
                if s == old_starts and e == old_ends and table_id in res["table_ids"]:
                    continue
                if starts_utc < e and ends_utc > s:
                    return error_response(409, "table_unavailable")
            # Check closures
            if is_closed(rid, table_id, starts_utc, ends_utc):
                return error_response(409, "table_unavailable")

        for table_id in res["table_ids"]:
            key = (rid, table_id)
            state.occupancy[key] = [(s, e) for s, e in state.occupancy.get(key, [])
                                    if not (s == old_starts and e == old_ends)]
        for table_id in table_ids:
            state.occupancy.setdefault((rid, table_id), []).append((starts_utc, ends_utc))

        res["table_ids"] = table_ids
        res["starts_at_local"] = starts_at_local
        res["starts_at"] = format_rfc3339(starts_utc)
        res["ends_at"] = format_rfc3339(ends_utc)
        res["party_size"] = party_size
        res["terms"] = terms
        res["revision"] += 1
        add_history(res, ZoneInfo(tz_name), "changed", diff_changes(old, new))

        # Mark as exception in series if part of one
        if res.get("series_id"):
            series_obj = state.series.get(res["series_id"])
            if series_obj:
                for member in series_obj["members"]:
                    if member["reference"] == res["reference"]:
                        member["exception"] = True
                        break
                series_obj["revision"] += 1

        # Increment restaurant revision for real amendment
        state.restaurant_revisions[rid] = state.restaurant_revisions.get(rid, 0) + 1

        return JSONResponse(format_reservation_response(res, tz_name))


async def publish_policy(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return error_response(400, "malformed_request")
    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    rid = request.path_params.get("id", "")
    restaurant = state.restaurants.get(rid)
    if restaurant is None:
        return error_response(404, "not_found")
    if user_id not in restaurant.get("manager_user_ids", []):
        return error_response(403, "forbidden")

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")
    if len(key) > 255:
        return error_response(422, "validation_failed")

    async with state.lock:
        path = f"/restaurants/{rid}/policies"
        idem_key = (user_id, path, key)
        recorded = state.idempotency.get(idem_key)
        body_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
        if recorded is not None:
            if body_json == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            return error_response(409, "idempotency_key_reuse")

        policy = validate_policy(body, restaurant)
        if policy is None:
            return error_response(422, "validation_failed")
        lst = state.policies.setdefault(rid, [])
        policy["policy_version"] = len(lst) + 1
        lst.append(policy)
        response_body = copy.deepcopy(policy)
        state.idempotency[idem_key] = {"method": "POST", "path": path, "body_json": body_json,
                                       "status": 201, "response": response_body}
        # Increment restaurant revision for policy publication
        state.restaurant_revisions[rid] = state.restaurant_revisions.get(rid, 0) + 1
        return JSONResponse(copy.deepcopy(policy), status_code=201)


async def list_policies(request: Request) -> JSONResponse:
    rid = request.path_params.get("id", "")
    if rid not in state.restaurants:
        return error_response(404, "not_found")
    return JSONResponse({"policies": copy.deepcopy(state.policies.get(rid, []))})


def visible_reservation(request: Request) -> Optional[dict]:
    """Reservation for the bearer token's owner; None for everything else (never 401)."""
    token = get_token_from_header(request)
    user_id = state.tokens.get(token) if token else None
    if user_id is None:
        return None
    return owned_reservation(user_id, request.path_params.get("reference", ""))


async def reservation_history(request: Request) -> JSONResponse:
    res = visible_reservation(request)
    if res is None:
        return error_response(404, "not_found")
    return JSONResponse({"reference": res["reference"], "entries": copy.deepcopy(res["history"])})


async def reservation_decision(request: Request) -> JSONResponse:
    res = visible_reservation(request)
    if res is None:
        return error_response(404, "not_found")
    return JSONResponse({"reference": res["reference"], "revision": res["revision"],
                         "accepted_terms": copy.deepcopy(res["terms"])})


def _valid_id(v) -> bool:
    return isinstance(v, str) and 0 < len(v) <= 64


def build_fixture_state(body: dict) -> Optional[dict]:
    """Validate a whole fixture and build fresh state. None if invalid."""
    users = body.get("users", [])
    restaurants = body.get("restaurants", [])
    reservations = body.get("reservations", [])
    for lst in (users, restaurants, reservations):
        if not isinstance(lst, list) or not all(isinstance(x, dict) for x in lst):
            return None

    new_users = {}
    seen_user_ids = set()
    for u in users:
        uid = u.get("id")
        if not _valid_id(uid) or uid in seen_user_ids:
            return None
        seen_user_ids.add(uid)
        email, password = u.get("email"), u.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            return None
        if not isinstance(u.get("display_name"), str):
            return None
        password_hash, password_salt = hash_password(password)
        new_users[uid] = {
            "id": uid,
            "email_lower": email.lower(),
            "password_hash": password_hash,
            "password_salt": password_salt,
            "display_name": u["display_name"],
        }

    new_restaurants = {}
    seen_rest_ids = set()
    for r in restaurants:
        rid = r.get("id")
        if not _valid_id(rid) or rid in seen_rest_ids:
            return None
        seen_rest_ids.add(rid)

        # Validate restaurant fields
        if not isinstance(r.get("timezone"), str):
            return None
        try:
            ZoneInfo(r["timezone"])
        except (KeyError, ValueError):
            return None

        slot_m = r.get("slot_minutes")
        dur_m = r.get("reservation_duration_minutes")
        if not isinstance(slot_m, int) or slot_m < 1 or not isinstance(dur_m, int) or dur_m < 1:
            return None

        # Validate opening hours
        seen_table_ids = set()
        for oh in r.get("opening_hours", []):
            if not isinstance(oh, dict):
                return None
            wd = oh.get("weekday")
            if wd not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
                return None
            opens, closes = oh.get("opens"), oh.get("closes")
            if not isinstance(opens, str) or not isinstance(closes, str):
                return None
            if not re.fullmatch(r'\d\d:\d\d', opens) or not re.fullmatch(r'\d\d:\d\d', closes):
                return None
            o_h, o_m = map(int, opens.split(':'))
            c_h, c_m = map(int, closes.split(':'))
            if o_h > 23 or o_m > 59 or c_h > 23 or c_m > 59:
                return None
            if datetime(2000, 1, 1, o_h, o_m) >= datetime(2000, 1, 1, c_h, c_m):
                return None

        # Validate tables
        for t in r.get("tables", []):
            if not isinstance(t, dict):
                return None
            tid = t.get("id")
            if not _valid_id(tid) or tid in seen_table_ids:
                return None
            seen_table_ids.add(tid)
            cap = t.get("capacity")
            if not isinstance(cap, int) or cap < 1:
                return None

        # Validate combinable pairs
        combinable = r.get("combinable", [])
        if not isinstance(combinable, list):
            return None
        for pair in combinable:
            if not isinstance(pair, list) or len(pair) != 2:
                return None
            t1, t2 = pair
            if not isinstance(t1, str) or not isinstance(t2, str):
                return None
            if t1 == t2:
                return None
            found1 = any(t["id"] == t1 for t in r.get("tables", []))
            found2 = any(t["id"] == t2 for t in r.get("tables", []))
            if not found1 or not found2:
                return None

        mgrs = r.get("manager_user_ids", [])
        if not isinstance(mgrs, list) or not all(isinstance(m, str) for m in mgrs):
            return None
        r.setdefault("manager_user_ids", [])

        new_restaurants[rid] = r

    new_res = {}
    new_occ = {}
    seen_res_ids = set()
    seen_refs = set()
    now = get_now_utc()
    for rd in reservations:
        rid, ref, uid = rd.get("id"), rd.get("reference"), rd.get("user_id")
        if not (_valid_id(rid) and _valid_id(uid)):
            return None
        if not isinstance(ref, str) or not re.fullmatch(r'[A-Z0-9]{6,12}', ref):
            return None
        if rid in seen_res_ids or ref in seen_refs:
            return None
        seen_res_ids.add(rid)
        seen_refs.add(ref)

        if uid not in new_users:
            return None
        restaurant = new_restaurants.get(rd.get("restaurant_id"))
        if restaurant is None:
            return None

        # Handle both table_id and table_ids
        table_ids = rd.get("table_ids")
        table_id = rd.get("table_id")
        if table_ids is not None:
            if not isinstance(table_ids, list) or len(table_ids) == 0:
                return None
            for tid in table_ids:
                if not isinstance(tid, str):
                    return None
                if not any(t["id"] == tid for t in restaurant["tables"]):
                    return None
            # Normalize pair order to declared order
            if len(table_ids) == 2:
                t1, t2 = table_ids
                found_pair = None
                for pair in restaurant.get("combinable", []):
                    if set(pair) == {t1, t2}:
                        found_pair = pair
                        break
                if not found_pair:
                    return None
                table_ids = list(found_pair)
        elif table_id is not None:
            if not isinstance(table_id, str):
                return None
            if not any(t["id"] == table_id for t in restaurant["tables"]):
                return None
            table_ids = [table_id]
        else:
            return None

        party = rd.get("party_size")
        if isinstance(party, bool) or not isinstance(party, int) or party < 1:
            return None
        try:
            starts_utc = from_local_time(rd.get("starts_at_local"), restaurant["timezone"])
            ends_utc = starts_utc + timedelta(minutes=restaurant["reservation_duration_minutes"])
        except (TypeError, KeyError, ValueError):
            return None
        if starts_utc is None:
            return None
        created = rd.get("created_at")
        if created is None:
            created = format_rfc3339(now)
        elif not isinstance(created, str):
            return None
        try:
            parse_rfc3339(created)
        except ValueError:
            return None

        status = rd.get("status", "confirmed")
        if status not in ("confirmed", "cancelled"):
            return None

        new_res[ref] = {
            "reservation_id": rid,
            "reference": ref,
            "restaurant_id": rd["restaurant_id"],
            "table_ids": table_ids,
            "party_size": party,
            "status": status,
            "starts_at_local": rd["starts_at_local"],
            "starts_at": format_rfc3339(starts_utc),
            "ends_at": format_rfc3339(ends_utc),
            "created_at": created,
            "user_id": uid,
        }
        init_synthesized(new_res[ref], restaurant)
        if status == "confirmed":
            for table_id in table_ids:
                new_occ.setdefault((rd["restaurant_id"], table_id), []).append((starts_utc, ends_utc))

    # Check for overlapping reservations
    for occupancies in new_occ.values():
        for i, (s1, e1) in enumerate(occupancies):
            for s2, e2 in occupancies[i+1:]:
                if s1 < e2 and s2 < e1:
                    return None

    # Initialize restaurant revisions to 0
    restaurant_revisions = {rid: 0 for rid in new_restaurants.keys()}

    return {"users": new_users, "restaurants": new_restaurants,
            "reservations": new_res, "occupancy": new_occ,
            "restaurant_revisions": restaurant_revisions}


async def reset(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    async with state.lock:
        built = build_fixture_state(body)
        if built is None:
            return error_response(422, "validation_failed")
        state.users = built["users"]
        state.tokens = {}
        state.restaurants = built["restaurants"]
        state.reservations = built["reservations"]
        state.idempotency = {}
        state.occupancy = built["occupancy"]
        state.policies = {}
        state.series = {}
        state.closures = {}
        state.replans = {}
        state.restaurant_revisions = built["restaurant_revisions"]

    return Response(status_code=204)


async def export(request: Request) -> JSONResponse:
    import copy
    async with state.lock:
        idem = [
            {"user_id": k[0], "path": k[1], "key": k[2], "method": v["method"],
             "body_json": v["body_json"], "status": v.get("status", 201),
             "response": v["response"]}
            for k, v in state.idempotency.items()
        ]
        export_data = {
            "track": "tablekeeper",
            "format_version": 1,
            "state": {
                "users": list(state.users.values()),
                "tokens": dict(state.tokens),
                "restaurants": list(state.restaurants.values()),
                "reservations": list(state.reservations.values()),
                "idempotency": idem,
                "policies": state.policies,
                "series": list(state.series.values()),
                "closures": {f"{rid}:{tid}": [(s.isoformat(), e.isoformat()) for s, e in intervals] for (rid, tid), intervals in state.closures.items()},
                "replans": list(state.replans.values()),
                "restaurant_revisions": dict(state.restaurant_revisions),
            },
        }
        export_data = copy.deepcopy(export_data)
    return JSONResponse(export_data)


def build_import_state(st) -> Optional[dict]:
    """Validate an exported state fully and rebuild runtime state. None if invalid."""
    import copy
    try:
        if not isinstance(st, dict):
            return None
        for k in ("users", "tokens", "restaurants", "reservations", "idempotency"):
            if k not in st:
                return None
        if not all(isinstance(st[k], list) for k in ("users", "restaurants", "reservations", "idempotency")):
            return None
        if not isinstance(st["tokens"], dict):
            return None
        st = copy.deepcopy(st)

        users = {}
        for u in st["users"]:
            if not isinstance(u, dict) or not _valid_id(u.get("id")) or u["id"] in users:
                return None
            for f in ("email_lower", "password_hash", "password_salt", "display_name"):
                if not isinstance(u.get(f), str):
                    return None
            bytes.fromhex(u["password_salt"])
            users[u["id"]] = u

        for t, uid in st["tokens"].items():
            if not isinstance(t, str) or uid not in users:
                return None

        restaurants = {}
        for r in st["restaurants"]:
            if not isinstance(r, dict) or not _valid_id(r.get("id")) or r["id"] in restaurants:
                return None
            for t in r.get("tables") or []:
                if not isinstance(t, dict) or not _valid_id(t.get("id")):
                    return None
            ZoneInfo(r["timezone"])
            for f in ("slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes"):
                if not isinstance(r[f], int) or isinstance(r[f], bool):
                    return None
            if not isinstance(r["opening_hours"], list) or not isinstance(r["tables"], list):
                return None
            restaurants[r["id"]] = r

        reservations = {}
        occupancy = {}
        for rd in st["reservations"]:
            if not isinstance(rd, dict):
                return None
            for f in ("reservation_id", "reference", "restaurant_id", "status",
                      "starts_at_local", "starts_at", "ends_at", "created_at", "user_id"):
                if not isinstance(rd.get(f), str):
                    return None
            if rd["reference"] in reservations or rd["restaurant_id"] not in restaurants:
                return None
            if rd["user_id"] not in users or rd["status"] not in ("confirmed", "cancelled"):
                return None
            if not isinstance(rd.get("party_size"), int) or isinstance(rd["party_size"], bool):
                return None

            # Accept both the single table_id form and the table_ids form
            table_id = rd.get("table_id")
            table_ids = rd.get("table_ids")
            if table_ids is not None:
                if not isinstance(table_ids, list) or len(table_ids) == 0:
                    return None
                for tid in table_ids:
                    if not isinstance(tid, str):
                        return None
            elif table_id is not None:
                if not isinstance(table_id, str):
                    return None
                table_ids = [table_id]
                rd["table_ids"] = table_ids
            else:
                return None

            starts, ends = parse_rfc3339(rd["starts_at"]), parse_rfc3339(rd["ends_at"])
            parse_rfc3339(rd["created_at"])
            rd.pop("table_id", None)
            if "history" not in rd or "terms" not in rd or "revision" not in rd:
                init_synthesized(rd, restaurants[rd["restaurant_id"]])
            elif not isinstance(rd["history"], list) or not isinstance(rd["terms"], dict) \
                    or not is_int(rd["revision"]):
                return None
            reservations[rd["reference"]] = rd
            if rd["status"] == "confirmed":
                for tid in table_ids:
                    occupancy.setdefault((rd["restaurant_id"], tid), []).append((starts, ends))

        idem = {}
        for rec in st["idempotency"]:
            if not isinstance(rec, dict):
                return None
            for f in ("user_id", "path", "key", "method", "body_json"):
                if not isinstance(rec.get(f), str):
                    return None
            if not isinstance(rec.get("response"), (dict, list)) or not isinstance(rec.get("status"), int):
                return None
            k = (rec["user_id"], rec["path"], rec["key"])
            if k in idem:
                return None
            idem[k] = {"method": rec["method"], "path": rec["path"], "body_json": rec["body_json"],
                       "status": rec["status"], "response": rec["response"]}
        policies = {}
        raw_pol = st.get("policies", {})
        if not isinstance(raw_pol, dict):
            return None
        for rid, lst in raw_pol.items():
            if rid not in restaurants or not isinstance(lst, list):
                return None
            out = []
            for i, p in enumerate(lst):
                if not isinstance(p, dict):
                    return None
                norm = validate_policy(p, restaurants[rid])
                if norm is None or p.get("policy_version") != i + 1:
                    return None
                norm["policy_version"] = i + 1
                out.append(norm)
            policies[rid] = out
        for r in restaurants.values():
            r.setdefault("manager_user_ids", [])

        series_objs = {}
        raw_series = st.get("series", [])
        if not isinstance(raw_series, list):
            return None
        for s_obj in raw_series:
            if not isinstance(s_obj, dict):
                return None
            for f in ("series_id", "owner", "interval_weeks", "revision"):
                if f not in s_obj:
                    return None
            series_id = s_obj.get("series_id")
            if not isinstance(series_id, str):
                return None
            owner = s_obj.get("owner")
            if owner not in users:
                return None
            if not is_int(s_obj["interval_weeks"]) or s_obj["interval_weeks"] < 1 or s_obj["interval_weeks"] > 4:
                return None
            if not is_int(s_obj["revision"]) or s_obj["revision"] < 1:
                return None
            members = s_obj.get("members", [])
            if not isinstance(members, list):
                return None
            for m in members:
                if not isinstance(m, dict) or not isinstance(m.get("reference"), str) or not isinstance(m.get("exception"), bool):
                    return None
                if m["reference"] not in reservations:
                    return None
            series_objs[series_id] = s_obj
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    except Exception:
        return None

    # Load closures
    closures = {}
    for key_str, intervals in st.get("closures", {}).items():
        if ":" not in key_str:
            continue
        rid, tid = key_str.split(":", 1)
        for s_str, e_str in intervals:
            try:
                s = parse_rfc3339(s_str) if "T" in s_str else datetime.fromisoformat(s_str).replace(tzinfo=timezone.utc)
                e = parse_rfc3339(e_str) if "T" in e_str else datetime.fromisoformat(e_str).replace(tzinfo=timezone.utc)
                closures.setdefault((rid, tid), []).append((s, e))
            except:
                pass

    # Load replans
    replans = {p.get("plan_id"): p for p in st.get("replans", []) if isinstance(p, dict) and "plan_id" in p}

    # Load restaurant revisions (default to 0 if missing)
    restaurant_revisions = {}
    for rid in restaurants.keys():
        restaurant_revisions[rid] = st.get("restaurant_revisions", {}).get(rid, 0)

    return {"users": users, "tokens": dict(st["tokens"]), "restaurants": restaurants,
            "reservations": reservations, "idempotency": idem, "occupancy": occupancy,
            "policies": policies, "series": series_objs, "closures": closures,
            "replans": replans, "restaurant_revisions": restaurant_revisions}


async def import_(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    if body.get("track") != "tablekeeper":
        return error_response(422, "validation_failed")
    fv = body.get("format_version")
    if fv != 1 or isinstance(fv, bool) or not isinstance(fv, int):
        return error_response(422, "validation_failed")
    if "state" not in body:
        return error_response(422, "validation_failed")

    async with state.lock:
        built = build_import_state(body["state"])
        if built is None:
            return error_response(422, "validation_failed")
        state.users = built["users"]
        state.tokens = built["tokens"]
        state.restaurants = built["restaurants"]
        state.reservations = built["reservations"]
        state.idempotency = built["idempotency"]
        state.occupancy = built["occupancy"]
        state.policies = built["policies"]
        state.series = built["series"]
        state.closures = built["closures"]
        state.replans = built["replans"]
        state.restaurant_revisions = built["restaurant_revisions"]

    return Response(status_code=204)


async def reservation_moves(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return error_response(400, "malformed_request")
    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")
    if len(key) > 255:
        return error_response(422, "validation_failed")

    async with state.lock:
        idem_key = (user_id, "/reservation-moves", key)
        recorded = state.idempotency.get(idem_key)
        if recorded is not None:
            if json.dumps(body, sort_keys=True, separators=(',', ':')) == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            return error_response(409, "idempotency_key_reuse")

        moves = body.get("moves")
        if not isinstance(moves, list) or not 1 <= len(moves) <= 8:
            return error_response(422, "validation_failed")
        refs = []
        for m in moves:
            if not isinstance(m, dict) or not isinstance(m.get("reference"), str):
                return error_response(422, "validation_failed")
            refs.append(m["reference"])
        if len(set(refs)) != len(refs):
            return error_response(422, "validation_failed")

        for m in moves:
            if "starts_at_local" in m and not isinstance(m["starts_at_local"], str):
                return error_response(400, "malformed_request")
            if "table_id" in m and not isinstance(m["table_id"], str):
                return error_response(400, "malformed_request")
            if "table_ids" in m and (not isinstance(m["table_ids"], list)
                                     or not all(isinstance(t, str) for t in m["table_ids"])):
                return error_response(400, "malformed_request")

        for m in moves:
            if "party_size" in m and (not is_int(m["party_size"]) or m["party_size"] < 1):
                return error_response(422, "validation_failed")
            if "expected_revision" in m and not valid_expected_revision(m["expected_revision"]):
                return error_response(422, "validation_failed")

        resv = []
        for ref in refs:
            r = owned_reservation(user_id, ref)
            if r is None:
                return error_response(404, "not_found")
            resv.append(r)
        if len({r["restaurant_id"] for r in resv}) != 1:
            return error_response(422, "validation_failed")

        restaurant = state.restaurants[resv[0]["restaurant_id"]]
        tz_name = restaurant["timezone"]
        rid = restaurant["id"]

        plans = []   # per item: None for a no-op, else (table_ids, local, party, starts, ends, terms)
        for m, r in zip(moves, resv):
            if "expected_revision" in m and m["expected_revision"] != r["revision"]:
                return error_response(409, "stale_revision")
            if r["status"] == "cancelled":
                return error_response(409, "reservation_cancelled")
            if cutoff_passed(r):
                return error_response(409, "cutoff_passed")

            table_ids = list(r["table_ids"])
            if "table_id" in m or "table_ids" in m:
                err, table_ids = validate_table_ids(m, restaurant)
                if err:
                    return error_response(*err)
            local = m.get("starts_at_local", r["starts_at_local"])
            party = m.get("party_size", r["party_size"])
            if (table_ids, local, party) == (list(r["table_ids"]), r["starts_at_local"], r["party_size"]):
                plans.append(None)
                continue
            err, starts_utc, ends_utc, terms = evaluate(restaurant, table_ids, local, party)
            if err:
                return err
            plans.append((table_ids, local, party, starts_utc, ends_utc, terms))

        remaining = {k: list(v) for k, v in state.occupancy.items()}
        for r, plan in zip(resv, plans):
            if plan is None:
                continue
            iv = (parse_rfc3339(r["starts_at"]), parse_rfc3339(r["ends_at"]))
            for table_id in r["table_ids"]:
                lst = remaining.get((rid, table_id), [])
                if iv in lst:
                    lst.remove(iv)
        for plan in plans:
            if plan is None:
                continue
            table_ids, _l, _p, s0, e0, _t = plan
            for table_id in table_ids:
                for s1, e1 in remaining.get((rid, table_id), []):
                    if s0 < e1 and e0 > s1:
                        return error_response(409, "table_unavailable")
                remaining.setdefault((rid, table_id), []).append((s0, e0))

        tz = ZoneInfo(tz_name)
        affected_series = set()
        for r, plan in zip(resv, plans):
            if plan is None:
                continue
            table_ids, local, party, s0, e0, terms = plan
            old = (list(r["table_ids"]), r["starts_at_local"], r["party_size"])
            r["table_ids"] = table_ids
            r["starts_at_local"] = local
            r["party_size"] = party
            r["starts_at"] = format_rfc3339(s0)
            r["ends_at"] = format_rfc3339(e0)
            r["terms"] = terms
            r["revision"] += 1
            add_history(r, tz, "changed", diff_changes(old, (table_ids, local, party)))
            # Mark as exception in series if part of one
            if r.get("series_id"):
                series_id = r["series_id"]
                affected_series.add(series_id)
                series_obj = state.series.get(series_id)
                if series_obj:
                    for member in series_obj["members"]:
                        if member["reference"] == r["reference"]:
                            member["exception"] = True
                            break
        # Increment affected series revisions once per batch
        for series_id in affected_series:
            series_obj = state.series.get(series_id)
            if series_obj:
                series_obj["revision"] += 1
        state.occupancy = remaining

        # Increment restaurant revision for moves batch with at least one real change
        if any(p is not None for p in plans):
            rid = resv[0]["restaurant_id"]
            state.restaurant_revisions[rid] = state.restaurant_revisions.get(rid, 0) + 1

        response_body = {"reservations": [format_reservation_response(r, tz_name) for r in resv]}
        state.idempotency[idem_key] = {
            "method": "POST",
            "path": "/reservation-moves",
            "body_json": json.dumps(body, sort_keys=True, separators=(',', ':')),
            "status": 201,
            "response": response_body,
        }
        return JSONResponse(response_body, status_code=201)


async def create_series(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")

    if len(key) > 255:
        return error_response(422, "validation_failed")

    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    async with state.lock:
        idempotency_key = (user_id, "/series", key)
        if idempotency_key in state.idempotency:
            recorded = state.idempotency[idempotency_key]
            current_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
            if current_json == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            else:
                return error_response(409, "idempotency_key_reuse")

        # Validate input fields
        anchor_ref = body.get("anchor_reference")
        count = body.get("count")
        interval_weeks = body.get("interval_weeks")

        if not isinstance(anchor_ref, str):
            return error_response(422, "validation_failed")
        if not is_int(count) or count < 2 or count > 12:
            return error_response(422, "validation_failed")
        if not is_int(interval_weeks) or interval_weeks < 1 or interval_weeks > 4:
            return error_response(422, "validation_failed")

        # Anchor validation
        if anchor_ref not in state.reservations:
            return error_response(404, "not_found")

        anchor = state.reservations[anchor_ref]
        if anchor["user_id"] != user_id:
            return error_response(404, "not_found")
        if anchor["status"] != "confirmed":
            return error_response(409, "reservation_cancelled")
        if anchor["series_id"] is not None:
            return error_response(409, "already_in_series")

        # Check cutoff
        restaurant = state.restaurants[anchor["restaurant_id"]]
        tz = ZoneInfo(restaurant["timezone"])
        now_utc = get_now_utc()
        now_local = now_utc.astimezone(tz)
        anchor_start = parse_rfc3339(anchor["starts_at"])
        anchor_cutoff_minutes = anchor["terms"]["cancellation_cutoff_minutes"]
        cutoff_time = anchor_start - timedelta(minutes=anchor_cutoff_minutes)
        if now_utc > cutoff_time:
            return error_response(409, "cutoff_passed")

        # Build occurrences
        anchor_date = anchor["starts_at_local"][:10]
        anchor_time = anchor["starts_at_local"][11:16]
        anchor_year, anchor_month, anchor_day = int(anchor_date[:4]), int(anchor_date[5:7]), int(anchor_date[8:10])
        anchor_hour, anchor_minute = int(anchor_time[:2]), int(anchor_time[3:5])

        occurrences = []
        occ_data_list = []  # Store all occurrence data before committing
        all_table_ids = anchor["table_ids"]
        party_size = anchor["party_size"]

        for i in range(count):
            if i == 0:
                # Anchor is unchanged
                occurrences.append({
                    "index": 0,
                    "reference": anchor_ref,
                    "exception": False,
                    "reservation": format_reservation_response(anchor, restaurant["timezone"]),
                })
                continue

            # Calculate next date with DST handling
            occ_date = datetime(anchor_year, anchor_month, anchor_day) + timedelta(days=i * interval_weeks * 7)
            occ_year, occ_month, occ_day = occ_date.year, occ_date.month, occ_date.day
            occ_local_str = f"{occ_year:04d}-{occ_month:02d}-{occ_day:02d}T{anchor_hour:02d}:{anchor_minute:02d}"

            # Try to build a reservation for this occurrence
            err, occ_start_utc, occ_end_utc, occ_terms = evaluate(restaurant, all_table_ids, occ_local_str, party_size)
            if err:
                # First failing index returns the error, nothing is stored
                return err

            # Check occupancy for all tables in the set (including previously generated occurrences)
            for tid in all_table_ids:
                if is_occupied(restaurant["id"], tid, occ_start_utc, occ_end_utc):
                    # Check if it conflicts with a previously generated occurrence
                    already_conflicted = False
                    for prev_occ in occ_data_list:
                        if tid in prev_occ["table_ids"]:
                            prev_start = prev_occ["starts_utc"]
                            prev_end = prev_occ["ends_utc"]
                            if occ_start_utc < prev_end and occ_end_utc > prev_start:
                                already_conflicted = True
                                break
                    if not already_conflicted:
                        return error_response(409, "table_unavailable")

            # Create the occurrence reservation
            occ_id = f"res_{uuid.uuid4().hex[:16]}"
            occ_ref = generate_reference()
            while occ_ref in state.reservations or any(od["reference"] == occ_ref for od in occ_data_list):
                occ_ref = generate_reference()

            occ_now_utc = get_now_utc()

            occ_res = {
                "reservation_id": occ_id,
                "reference": occ_ref,
                "restaurant_id": restaurant["id"],
                "table_ids": all_table_ids,
                "party_size": party_size,
                "status": "confirmed",
                "starts_at_local": occ_local_str,
                "starts_at": format_rfc3339(occ_start_utc),
                "ends_at": format_rfc3339(occ_end_utc),
                "created_at": format_rfc3339(occ_now_utc),
                "user_id": user_id,
                "revision": 1,
                "terms": occ_terms,
                "history": [],
                "series_id": None,  # Will be set after series is created
            }

            add_history(occ_res, tz, "created", created_changes(all_table_ids, occ_local_str, party_size), at=occ_now_utc)
            occurrences.append({
                "index": i,
                "reference": occ_ref,
                "exception": False,
                "reservation": format_reservation_response(occ_res, restaurant["timezone"]),
            })

            # Store data for later commit
            occ_data_list.append({
                "reference": occ_ref,
                "table_ids": all_table_ids,
                "starts_utc": occ_start_utc,
                "ends_utc": occ_end_utc,
                "res": occ_res,
            })

        # Commit all occurrences to state
        for occ_data in occ_data_list:
            occ_res = occ_data["res"]
            occ_ref = occ_data["reference"]
            state.reservations[occ_ref] = occ_res
            for tid in occ_data["table_ids"]:
                state.occupancy.setdefault((restaurant["id"], tid), []).append((occ_data["starts_utc"], occ_data["ends_utc"]))

        # Create the series
        series_id = f"ser_{uuid.uuid4().hex[:16]}"
        series_obj = {
            "series_id": series_id,
            "owner": user_id,
            "restaurant_id": anchor["restaurant_id"],
            "interval_weeks": interval_weeks,
            "revision": 1,
            "members": [],
        }

        # Link all occurrences to the series
        anchor["series_id"] = series_id
        for i, occ_data in enumerate(occurrences):
            if i == 0:
                member = {"index": 0, "reference": anchor_ref, "exception": False}
            else:
                occ_ref = occ_data["reference"]
                occ_res = state.reservations[occ_ref]
                occ_res["series_id"] = series_id
                member = {"index": i, "reference": occ_ref, "exception": False}
            series_obj["members"].append(member)

        state.series[series_id] = series_obj

        # Increment restaurant revision for series adoption
        rid = anchor["restaurant_id"]
        state.restaurant_revisions[rid] = state.restaurant_revisions.get(rid, 0) + 1

        response_body = {
            "series_id": series_id,
            "revision": 1,
            "interval_weeks": interval_weeks,
            "occurrences": occurrences,
        }

        state.idempotency[idempotency_key] = {
            "method": "POST",
            "path": "/series",
            "body_json": json.dumps(body, sort_keys=True, separators=(',', ':')),
            "response": response_body,
        }

        return JSONResponse(response_body, status_code=201)


async def get_series(request: Request) -> JSONResponse:
    user_id, _ = await require_auth(request)

    series_id = request.path_params.get("id", "")

    async with state.lock:
        if series_id not in state.series:
            return error_response(404, "not_found")

        series_obj = state.series[series_id]
        if user_id is None or series_obj["owner"] != user_id:
            return error_response(404, "not_found")

        occurrences = []
        for member in series_obj["members"]:
            ref = member["reference"]
            if ref not in state.reservations:
                return error_response(500, "internal_error")

            res = state.reservations[ref]
            restaurant = state.restaurants[res["restaurant_id"]]
            occ_data = {
                "index": member["index"],
                "reference": ref,
                "exception": member["exception"],
                "reservation": format_reservation_response(res, restaurant["timezone"]),
            }
            occurrences.append(occ_data)

        response = {
            "series_id": series_id,
            "revision": series_obj["revision"],
            "interval_weeks": series_obj["interval_weeks"],
            "occurrences": occurrences,
        }

        return JSONResponse(response, status_code=200)


async def preview_replan(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    rid = request.path_params.get("id", "")
    restaurant = state.restaurants.get(rid)
    if restaurant is None:
        return error_response(404, "not_found")
    if user_id not in restaurant.get("manager_user_ids", []):
        return error_response(403, "forbidden")

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")
    if len(key) > 255:
        return error_response(422, "validation_failed")

    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    async with state.lock:
        path = f"/restaurants/{rid}/replans"
        idem_key = (user_id, path, key)
        recorded = state.idempotency.get(idem_key)
        body_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
        if recorded is not None:
            if body_json == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            return error_response(409, "idempotency_key_reuse")

        # Validate input
        table_id = body.get("table_id")
        from_str = body.get("from")
        to_str = body.get("to")

        if not isinstance(table_id, str):
            return error_response(422, "validation_failed")
        if not isinstance(from_str, str) or not isinstance(to_str, str):
            return error_response(422, "validation_failed")

        # Parse RFC3339 timestamps
        try:
            from_utc = parse_rfc3339(from_str)
            to_utc = parse_rfc3339(to_str)
        except:
            return error_response(422, "validation_failed")

        if from_utc >= to_utc:
            return error_response(422, "validation_failed")

        # Check if table exists
        if not any(t["id"] == table_id for t in restaurant.get("tables", [])):
            return error_response(404, "not_found")

        # Find considered bookings (overlapping the closure)
        considered = []
        for ref, res in state.reservations.items():
            if res["restaurant_id"] != rid or res["status"] != "confirmed":
                continue
            res_start = parse_rfc3339(res["starts_at"])
            res_end = parse_rfc3339(res["ends_at"])
            if res_start < to_utc and res_end > from_utc:
                considered.append(ref)

        # Check planning limits
        if len(restaurant.get("tables", [])) > 6 or len(restaurant.get("combinable", [])) > 4 or len(considered) > 6:
            return error_response(422, "planning_limit")

        # Build table rankings
        tables = restaurant.get("tables", [])
        rankings = {}
        idx = 0
        for t in tables:
            rankings[tuple([t["id"]])] = idx
            idx += 1
        for pair in restaurant.get("combinable", []):
            rankings[tuple(sorted(pair))] = idx
            idx += 1

        # Simple greedy algorithm for now
        considered.sort()
        assignments = {}
        for ref in considered:
            res = state.reservations[ref]
            current_tables = tuple(sorted(res["table_ids"]))

            # Try to keep current assignment
            if rankings.get(current_tables, 999) < 999:
                capacity = sum(t["capacity"] for t in tables if t["id"] in res["table_ids"])
                if capacity >= res["party_size"]:
                    assignments[ref] = (current_tables, False)
                    continue

            # Find best assignment
            best_option = None
            best_rank = 999
            for t in tables:
                if t["id"] == table_id:
                    continue
                key = tuple([t["id"]])
                if rankings.get(key, 999) < best_rank and t["capacity"] >= res["party_size"]:
                    best_option = key
                    best_rank = rankings.get(key, 999)

            if best_option:
                assignments[ref] = (best_option, True)
            else:
                return error_response(409, "no_feasible_plan")

        # Generate plan
        plan_id = f"plan_{uuid.uuid4().hex[:16]}"
        moved_count = sum(1 for ref in considered if assignments[ref][1])
        unused_seats = sum(sum(t["capacity"] for t in tables if t["id"] in assignments[ref][0]) - state.reservations[ref]["party_size"] for ref in considered)

        plan = {
            "plan_id": plan_id,
            "restaurant_id": rid,
            "revision": state.restaurant_revisions.get(rid, 0),
            "closure": {"table_id": table_id, "from": from_str, "to": to_str},
            "assignments": [{"reference": ref, "table_ids": list(assignments[ref][0]), "changed": assignments[ref][1]} for ref in considered],
            "applied": False,
        }
        state.replans[plan_id] = plan

        response_body = {
            "plan_id": plan_id,
            "restaurant_revision": state.restaurant_revisions.get(rid, 0),
            "closure": plan["closure"],
            "assignments": plan["assignments"],
            "moved_count": moved_count,
            "unused_seats": unused_seats,
        }

        state.idempotency[idem_key] = {
            "method": "POST",
            "path": path,
            "body_json": body_json,
            "status": 201,
            "response": response_body,
        }

        return JSONResponse(response_body, status_code=201)


async def apply_replan(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    rid = request.path_params.get("id", "")
    plan_id = request.path_params.get("plan_id", "")

    restaurant = state.restaurants.get(rid)
    if restaurant is None:
        return error_response(404, "not_found")
    if user_id not in restaurant.get("manager_user_ids", []):
        return error_response(403, "forbidden")

    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")
    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")

    async with state.lock:
        path = f"/restaurants/{rid}/replans/{plan_id}/apply"
        idem_key = (user_id, path, key)
        recorded = state.idempotency.get(idem_key)
        if recorded is not None:
            return JSONResponse(recorded["response"], status_code=200)

        if plan_id not in state.replans:
            return error_response(404, "not_found")

        plan = state.replans[plan_id]
        if plan["restaurant_id"] != rid:
            return error_response(404, "not_found")
        if plan.get("applied"):
            return error_response(409, "plan_already_applied")
        if plan["revision"] != state.restaurant_revisions.get(rid, 0):
            return error_response(409, "stale_plan")

        # Apply the plan
        tz = ZoneInfo(restaurant["timezone"])
        closure = plan["closure"]
        from_utc = parse_rfc3339(closure["from"])
        to_utc = parse_rfc3339(closure["to"])

        # Record closure
        state.closures.setdefault((rid, closure["table_id"]), []).append((from_utc, to_utc))

        # Apply assignments
        reservations = []
        affected_series = set()
        for assignment in plan["assignments"]:
            ref = assignment["reference"]
            res = state.reservations[ref]
            if assignment["changed"]:
                # Remove old occupancy
                old_start = parse_rfc3339(res["starts_at"])
                old_end = parse_rfc3339(res["ends_at"])
                for tid in res["table_ids"]:
                    key = (rid, tid)
                    if key in state.occupancy:
                        state.occupancy[key] = [(s, e) for s, e in state.occupancy[key] if not (s == old_start and e == old_end)]

                # Update reservation
                old_table_ids = list(res["table_ids"])
                res["table_ids"] = assignment["table_ids"]
                res["revision"] += 1
                add_history(res, tz, "reassigned", [{"field": "table_ids", "from": old_table_ids, "to": assignment["table_ids"]}], plan_id=plan_id)

                # Add new occupancy
                for tid in assignment["table_ids"]:
                    state.occupancy.setdefault((rid, tid), []).append((old_start, old_end))

                # Track series
                if res.get("series_id"):
                    affected_series.add(res["series_id"])

            reservations.append(format_reservation_response(res, restaurant["timezone"]))

        # Increment series revisions
        for series_id in affected_series:
            series_obj = state.series.get(series_id)
            if series_obj:
                series_obj["revision"] += 1

        # Mark plan as applied
        plan["applied"] = True

        # Increment restaurant revision
        state.restaurant_revisions[rid] = state.restaurant_revisions.get(rid, 0) + 1

        response_body = {
            "plan_id": plan_id,
            "restaurant_revision": state.restaurant_revisions[rid],
            "reservations": reservations,
        }

        state.idempotency[idem_key] = {
            "method": "POST",
            "path": path,
            "body_json": json.dumps(body, sort_keys=True, separators=(',', ':')),
            "status": 201,
            "response": response_body,
        }

        return JSONResponse(response_body, status_code=201)


async def not_found(request: Request) -> JSONResponse:
    return error_response(404, "not_found")


async def serve_index(request: Request) -> Response:
    """Serve index.html for HTML routes."""
    try:
        with open(os.path.join(os.path.dirname(__file__), 'static', 'index.html'), 'r') as f:
            html = f.read()
        return Response(html, media_type="text/html; charset=utf-8")
    except:
        return error_response(404, "not_found")


async def serve_static(request: Request) -> Response:
    """Serve static files (CSS, JS)."""
    path = request.path_params.get("path", "")
    file_path = os.path.join(os.path.dirname(__file__), 'static', path)

    if not os.path.isfile(file_path):
        return error_response(404, "not_found")

    try:
        with open(file_path, 'rb') as f:
            content = f.read()

        if path.endswith('.css'):
            media_type = "text/css; charset=utf-8"
        elif path.endswith('.js'):
            media_type = "text/javascript; charset=utf-8"
        else:
            media_type = "application/octet-stream"

        return Response(content, media_type=media_type)
    except:
        return error_response(404, "not_found")


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/_test/reset", reset, methods=["POST"]),
    Route("/_test/export", export, methods=["GET"]),
    Route("/_test/import", import_, methods=["POST"]),
    Route("/auth/signup", auth_signup, methods=["POST"]),
    Route("/auth/login", auth_login, methods=["POST"]),
    Route("/restaurants", get_restaurants, methods=["GET"]),
    Route("/restaurants/{id}", get_restaurant, methods=["GET"]),
    Route("/availability", get_availability, methods=["GET"]),
    Route("/reservations", create_reservation, methods=["POST"]),
    Route("/reservations", list_reservations, methods=["GET"]),
    Route("/reservations/{reference}", get_reservation, methods=["GET"]),
    Route("/reservations/{reference}/cancel", cancel_reservation, methods=["POST"]),
    Route("/reservations/{reference}/history", reservation_history, methods=["GET"]),
    Route("/reservations/{reference}/decision", reservation_decision, methods=["GET"]),
    Route("/restaurants/{id}/policies", publish_policy, methods=["POST"]),
    Route("/restaurants/{id}/policies", list_policies, methods=["GET"]),
    Route("/reservations/{reference}", patch_reservation, methods=["PATCH"]),
    Route("/reservation-moves", reservation_moves, methods=["POST"]),
    Route("/series", create_series, methods=["POST"]),
    Route("/series/{id}", get_series, methods=["GET"]),
    Route("/restaurants/{id}/replans", preview_replan, methods=["POST"]),
    Route("/restaurants/{id}/replans/{plan_id}/apply", apply_replan, methods=["POST"]),
    Route("/static/{path:path}", serve_static, methods=["GET"]),
    Route("/", serve_index, methods=["GET"]),
    Route("/signup", serve_index, methods=["GET"]),
    Route("/login", serve_index, methods=["GET"]),
    Route("/lookup", serve_index, methods=["GET"]),
    Route("/{path:path}", not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
