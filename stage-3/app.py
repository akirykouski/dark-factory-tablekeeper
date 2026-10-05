"""Tablekeeper reservations API."""
from __future__ import annotations

import asyncio
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
        "table_ids": table_ids,
        "party_size": res["party_size"],
        "status": res["status"],
        "starts_at_local": res["starts_at_local"],
        "starts_at": format_rfc3339(starts_utc.astimezone(tz)),
        "ends_at": format_rfc3339(ends_utc.astimezone(tz)),
        "created_at": res["created_at"],
    }
    if len(table_ids) == 1:
        resp["table_id"] = table_ids[0]
    return resp


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
    return JSONResponse(resp)


def build_available_options(restaurant: dict, slot_utc: datetime, slot_end_utc: datetime, party_size: int) -> list:
    """Build available_options: singles first, then pairs."""
    options = []

    # Singles first in fixture order
    for table in restaurant["tables"]:
        if table["capacity"] >= party_size:
            occupied = False
            key = (restaurant["id"], table["id"])
            if key in state.occupancy:
                for s, e in state.occupancy[key]:
                    if slot_utc < e and slot_end_utc > s:
                        occupied = True
                        break
            if not occupied:
                options.append({"table_ids": [table["id"]], "capacity": table["capacity"]})

    # Then pairs in combinable order
    for pair in restaurant.get("combinable", []):
        t1_id, t2_id = pair
        t1 = next((t for t in restaurant["tables"] if t["id"] == t1_id), None)
        t2 = next((t for t in restaurant["tables"] if t["id"] == t2_id), None)
        if not t1 or not t2:
            continue
        capacity = t1["capacity"] + t2["capacity"]
        if capacity >= party_size:
            occupied1 = False
            occupied2 = False
            key1 = (restaurant["id"], t1_id)
            key2 = (restaurant["id"], t2_id)
            if key1 in state.occupancy:
                for s, e in state.occupancy[key1]:
                    if slot_utc < e and slot_end_utc > s:
                        occupied1 = True
                        break
            if key2 in state.occupancy:
                for s, e in state.occupancy[key2]:
                    if slot_utc < e and slot_end_utc > s:
                        occupied2 = True
                        break
            if not occupied1 and not occupied2:
                options.append({"table_ids": list(pair), "capacity": capacity})

    return options


async def get_availability(request: Request) -> JSONResponse:
    restaurant_id = request.query_params.get("restaurant_id")
    date = request.query_params.get("date")
    party_size = request.query_params.get("party_size")

    if not restaurant_id or not date or not party_size:
        return error_response(422, "validation_failed")

    if not re.fullmatch(r'\d+', party_size):
        return error_response(422, "validation_failed")

    party_size_int = int(party_size)
    if party_size_int < 1:
        return error_response(422, "validation_failed")

    if not re.fullmatch(r'\d{4}-\d\d-\d\d', date):
        return error_response(422, "validation_failed")

    try:
        year, month, day = map(int, date.split('-'))
        datetime(year, month, day)
    except ValueError:
        return error_response(422, "validation_failed")

    if restaurant_id not in state.restaurants:
        return error_response(404, "not_found")

    restaurant = state.restaurants[restaurant_id]
    tz_name = restaurant["timezone"]
    slot_minutes = restaurant["slot_minutes"]
    duration_minutes = restaurant["reservation_duration_minutes"]

    weekday_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    weekday = weekday_names[datetime(year, month, day).weekday()]

    opens_str = None
    closes_str = None
    for oh in restaurant["opening_hours"]:
        if oh["weekday"] == weekday:
            opens_str = oh["opens"]
            closes_str = oh["closes"]
            break

    slots = []
    if opens_str and closes_str:
        oh_h, oh_m = map(int, opens_str.split(':'))
        ch_h, ch_m = map(int, closes_str.split(':'))

        tz = ZoneInfo(tz_name)
        current_min = oh_h * 60 + oh_m
        closes_total_min = ch_h * 60 + ch_m

        while current_min < closes_total_min:
            slot_h = current_min // 60
            slot_m = current_min % 60

            slot_end_min = current_min + duration_minutes
            if slot_end_min > closes_total_min:
                break

            # Check if time exists
            naive = datetime(year, month, day, slot_h, slot_m)
            try:
                test = naive.replace(fold=0, tzinfo=tz)
                if test.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:
                    current_min += slot_minutes
                    continue
            except:
                current_min += slot_minutes
                continue

            local_slot = naive.replace(tzinfo=tz)
            slot_utc = local_slot.astimezone(timezone.utc)
            slot_end_utc = slot_utc + timedelta(minutes=duration_minutes)

            available = []
            for table in restaurant["tables"]:
                if table["capacity"] >= party_size_int:
                    occupied = False
                    key = (restaurant_id, table["id"])
                    if key in state.occupancy:
                        for s, e in state.occupancy[key]:
                            if slot_utc < e and slot_end_utc > s:
                                occupied = True
                                break

                    if not occupied:
                        available.append(table["id"])

            available_options = build_available_options(restaurant, slot_utc, slot_end_utc, party_size_int)

            slots.append({
                "starts_at_local": f"{date}T{slot_h:02d}:{slot_m:02d}",
                "starts_at": format_rfc3339(slot_utc.astimezone(tz)),
                "available_table_ids": available,
                "available_options": available_options,
            })

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

    # ATOMIC: check idempotency + create + store within lock
    async with state.lock:
        idempotency_key = (user_id, "/reservations", key)
        if idempotency_key in state.idempotency:
            recorded = state.idempotency[idempotency_key]
            current_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
            if current_json == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            else:
                return error_response(409, "idempotency_key_reuse")

        # Validate required fields
        if "restaurant_id" not in body or "starts_at_local" not in body or "party_size" not in body:
            return error_response(422, "validation_failed")

        restaurant_id = body["restaurant_id"]
        starts_at_local = body["starts_at_local"]
        party_size = body["party_size"]

        if not isinstance(restaurant_id, str):
            return error_response(400, "malformed_request")
        if not isinstance(starts_at_local, str):
            return error_response(400, "malformed_request")
        if party_size is None or not isinstance(party_size, int) or isinstance(party_size, bool):
            return error_response(422, "validation_failed")

        if party_size < 1:
            return error_response(422, "validation_failed")

        if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d', starts_at_local):
            return error_response(422, "validation_failed")

        if restaurant_id not in state.restaurants:
            return error_response(404, "not_found")

        restaurant = state.restaurants[restaurant_id]

        # Validate table_id or table_ids
        err, table_ids = validate_table_ids(body, restaurant)
        if err:
            status, code = err
            return error_response(status, code)

        tz_name = restaurant["timezone"]

        # Validate starts_at_local format and values before parsing
        try:
            date_str, time_str = starts_at_local.split('T')
            year, month, day = map(int, date_str.split('-'))
            hour, minute = map(int, time_str.split(':'))
            if hour > 23 or minute > 59:
                return error_response(422, "validation_failed")
            datetime(year, month, day, hour, minute)
        except (ValueError, IndexError):
            return error_response(422, "validation_failed")

        starts_utc = from_local_time(starts_at_local, tz_name)
        if starts_utc is None:
            return error_response(422, "invalid_local_time")

        date_part = starts_at_local[:10]
        year, month, day = map(int, date_part.split('-'))
        weekday_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        weekday = weekday_names[datetime(year, month, day).weekday()]

        opens_str = None
        closes_str = None
        for oh in restaurant["opening_hours"]:
            if oh["weekday"] == weekday:
                opens_str = oh["opens"]
                closes_str = oh["closes"]
                break

        if not opens_str or not closes_str:
            return error_response(422, "outside_opening_hours")

        tz = ZoneInfo(tz_name)
        hour, minute = map(int, starts_at_local[11:].split(':'))
        local_dt = datetime(year, month, day, hour, minute, tzinfo=tz)

        oh_h, oh_m = map(int, opens_str.split(':'))
        opens_dt = datetime(year, month, day, oh_h, oh_m, tzinfo=tz)

        ch_h, ch_m = map(int, closes_str.split(':'))
        closes_dt = datetime(year, month, day, ch_h, ch_m, tzinfo=tz)

        if local_dt < opens_dt:
            return error_response(422, "outside_opening_hours")

        duration = restaurant["reservation_duration_minutes"]
        ends_utc = starts_utc + timedelta(minutes=duration)
        ends_local = ends_utc.astimezone(tz)

        if ends_local > closes_dt:
            return error_response(422, "outside_opening_hours")

        slot_minutes = restaurant["slot_minutes"]
        mins_from_open = int((local_dt - opens_dt).total_seconds() / 60)
        if mins_from_open % slot_minutes != 0:
            return error_response(422, "not_on_slot_grid")

        # Calculate capacity for the set
        capacity = sum(t["capacity"] for t in restaurant["tables"] if t["id"] in table_ids)
        if party_size > capacity:
            return error_response(422, "party_exceeds_capacity")

        # Check occupancy for all tables in the set
        for table_id in table_ids:
            key_occ = (restaurant_id, table_id)
            if key_occ in state.occupancy:
                for s, e in state.occupancy[key_occ]:
                    if starts_utc < e and ends_utc > s:
                        return error_response(409, "table_unavailable")

        # Create reservation
        reservation_id = f"res_{uuid.uuid4().hex[:16]}"
        reference = generate_reference()
        while reference in state.reservations:
            reference = generate_reference()

        now_utc = get_now_utc()

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
        }

        state.reservations[reference] = reservation
        for table_id in table_ids:
            key_occ = (restaurant_id, table_id)
            if key_occ not in state.occupancy:
                state.occupancy[key_occ] = []
            state.occupancy[key_occ].append((starts_utc, ends_utc))

        response_body = format_reservation_response(reservation, tz_name)

        body_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
        state.idempotency[idempotency_key] = {
            "method": "POST",
            "path": "/reservations",
            "body_json": body_json,
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

    reference = request.path_params.get("reference", "")

    if reference not in state.reservations:
        return error_response(404, "not_found")

    res = state.reservations[reference]
    if res["user_id"] != user_id:
        return error_response(404, "not_found")

    if res["status"] == "cancelled":
        tz_name = state.restaurants[res["restaurant_id"]]["timezone"]
        return JSONResponse(format_reservation_response(res, tz_name))

    restaurant = state.restaurants[res["restaurant_id"]]
    cutoff_minutes = restaurant["cancellation_cutoff_minutes"]
    starts_utc = parse_rfc3339(res["starts_at"])
    now_utc = get_now_utc()

    if now_utc >= starts_utc - timedelta(minutes=cutoff_minutes):
        return error_response(409, "cutoff_passed")

    res["status"] = "cancelled"

    starts = parse_rfc3339(res["starts_at"])
    ends = parse_rfc3339(res["ends_at"])
    for table_id in res.get("table_ids", []):
        key = (res["restaurant_id"], table_id)
        if key in state.occupancy:
            state.occupancy[key] = [(s, e) for s, e in state.occupancy[key] if not (s == starts and e == ends)]

    tz_name = state.restaurants[res["restaurant_id"]]["timezone"]
    return JSONResponse(format_reservation_response(res, tz_name))


async def patch_reservation(request: Request) -> JSONResponse:
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    reference = request.path_params.get("reference", "")

    if reference not in state.reservations:
        return error_response(404, "not_found")

    res = state.reservations[reference]
    if res["user_id"] != user_id:
        return error_response(404, "not_found")

    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    if res["status"] == "cancelled":
        return error_response(409, "reservation_cancelled")

    restaurant_id = res["restaurant_id"]
    table_ids = res.get("table_ids", [])
    starts_at_local = res["starts_at_local"]
    party_size = res["party_size"]

    # Handle table_id/table_ids in PATCH
    if "table_id" in body or "table_ids" in body:
        err, new_table_ids = validate_table_ids(body, state.restaurants[restaurant_id])
        if err:
            status, code = err
            return error_response(status, code)
        table_ids = new_table_ids

    if "starts_at_local" in body:
        starts_at_local = body["starts_at_local"]
    if "party_size" in body:
        party_size = body["party_size"]

    if "starts_at_local" in body and not isinstance(starts_at_local, str):
        return error_response(400, "malformed_request")
    if "party_size" in body:
        if party_size is None or not isinstance(party_size, int) or isinstance(party_size, bool):
            return error_response(422, "validation_failed")

    if party_size < 1:
        return error_response(422, "validation_failed")

    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d', starts_at_local):
        return error_response(422, "validation_failed")

    restaurant = state.restaurants[restaurant_id]
    cutoff_minutes = restaurant["cancellation_cutoff_minutes"]
    current_starts_utc = parse_rfc3339(res["starts_at"])
    now_utc = get_now_utc()

    if now_utc >= current_starts_utc - timedelta(minutes=cutoff_minutes):
        return error_response(409, "cutoff_passed")

    tz_name = restaurant["timezone"]
    starts_utc = from_local_time(starts_at_local, tz_name)
    if starts_utc is None:
        return error_response(422, "invalid_local_time")

    date_part = starts_at_local[:10]
    year, month, day = map(int, date_part.split('-'))
    weekday_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    weekday = weekday_names[datetime(year, month, day).weekday()]

    opens_str = None
    closes_str = None
    for oh in restaurant["opening_hours"]:
        if oh["weekday"] == weekday:
            opens_str = oh["opens"]
            closes_str = oh["closes"]
            break

    if not opens_str or not closes_str:
        return error_response(422, "outside_opening_hours")

    tz = ZoneInfo(tz_name)
    hour, minute = map(int, starts_at_local[11:].split(':'))
    local_dt = datetime(year, month, day, hour, minute, tzinfo=tz)

    oh_h, oh_m = map(int, opens_str.split(':'))
    opens_dt = datetime(year, month, day, oh_h, oh_m, tzinfo=tz)

    ch_h, ch_m = map(int, closes_str.split(':'))
    closes_dt = datetime(year, month, day, ch_h, ch_m, tzinfo=tz)

    if local_dt < opens_dt:
        return error_response(422, "outside_opening_hours")

    duration = restaurant["reservation_duration_minutes"]
    ends_utc = starts_utc + timedelta(minutes=duration)
    ends_local = ends_utc.astimezone(tz)

    if ends_local > closes_dt:
        return error_response(422, "outside_opening_hours")

    slot_minutes = restaurant["slot_minutes"]
    mins_from_open = int((local_dt - opens_dt).total_seconds() / 60)
    if mins_from_open % slot_minutes != 0:
        return error_response(422, "not_on_slot_grid")

    # Calculate capacity
    capacity = sum(t["capacity"] for t in restaurant["tables"] if t["id"] in table_ids)
    if party_size > capacity:
        return error_response(422, "party_exceeds_capacity")

    old_table_ids = res.get("table_ids", [])
    old_starts = parse_rfc3339(res["starts_at"])
    old_ends = parse_rfc3339(res["ends_at"])

    # Check occupancy for new tables (excluding own booking)
    for table_id in table_ids:
        key = (restaurant_id, table_id)
        if key in state.occupancy:
            for s, e in state.occupancy[key]:
                if s == old_starts and e == old_ends and table_id in old_table_ids:
                    continue
                if starts_utc < e and ends_utc > s:
                    return error_response(409, "table_unavailable")

    res["table_ids"] = table_ids
    res["starts_at_local"] = starts_at_local
    res["starts_at"] = format_rfc3339(starts_utc)
    res["ends_at"] = format_rfc3339(ends_utc)
    res["party_size"] = party_size

    # Update occupancy
    for table_id in old_table_ids:
        key = (restaurant_id, table_id)
        if key in state.occupancy:
            state.occupancy[key] = [(s, e) for s, e in state.occupancy[key] if not (s == old_starts and e == old_ends)]

    for table_id in table_ids:
        key = (restaurant_id, table_id)
        if key not in state.occupancy:
            state.occupancy[key] = []
        state.occupancy[key].append((starts_utc, ends_utc))

    return JSONResponse(format_reservation_response(res, tz_name))


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
        if status == "confirmed":
            for table_id in table_ids:
                new_occ.setdefault((rd["restaurant_id"], table_id), []).append((starts_utc, ends_utc))

    # Check for overlapping reservations
    for occupancies in new_occ.values():
        for i, (s1, e1) in enumerate(occupancies):
            for s2, e2 in occupancies[i+1:]:
                if s1 < e2 and s2 < e1:
                    return None

    return {"users": new_users, "restaurants": new_restaurants,
            "reservations": new_res, "occupancy": new_occ}


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
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    except Exception:
        return None
    return {"users": users, "tokens": dict(st["tokens"]), "restaurants": restaurants,
            "reservations": reservations, "idempotency": idem, "occupancy": occupancy}


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

    return Response(status_code=204)


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def validate_amendment(restaurant: dict, table_ids: list, starts_at_local: str, party_size: int):
    """Amendment checks on resulting values. Returns (error|None, starts_utc, ends_utc)."""
    if party_size < 1 or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d', starts_at_local):
        return error_response(422, "validation_failed"), None, None
    try:
        year, month, day = map(int, starts_at_local[:10].split('-'))
        hour, minute = map(int, starts_at_local[11:].split(':'))
        if hour > 23 or minute > 59:
            return error_response(422, "validation_failed"), None, None
        datetime(year, month, day, hour, minute)
    except ValueError:
        return error_response(422, "validation_failed"), None, None

    # Validate all tables exist
    for table_id in table_ids:
        table = next((t for t in restaurant["tables"] if t["id"] == table_id), None)
        if table is None:
            return error_response(404, "not_found"), None, None

    tz = ZoneInfo(restaurant["timezone"])
    starts_utc = from_local_time(starts_at_local, restaurant["timezone"])
    if starts_utc is None:
        return error_response(422, "invalid_local_time"), None, None

    oh = next((o for o in restaurant["opening_hours"]
               if o["weekday"] == WEEKDAYS[datetime(year, month, day).weekday()]), None)
    if oh is None:
        return error_response(422, "outside_opening_hours"), None, None

    local_dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    oh_h, oh_m = map(int, oh["opens"].split(':'))
    ch_h, ch_m = map(int, oh["closes"].split(':'))
    opens_dt = datetime(year, month, day, oh_h, oh_m, tzinfo=tz)
    closes_dt = datetime(year, month, day, ch_h, ch_m, tzinfo=tz)
    if local_dt < opens_dt:
        return error_response(422, "outside_opening_hours"), None, None

    ends_utc = starts_utc + timedelta(minutes=restaurant["reservation_duration_minutes"])
    if ends_utc.astimezone(tz) > closes_dt:
        return error_response(422, "outside_opening_hours"), None, None

    if int((local_dt - opens_dt).total_seconds() / 60) % restaurant["slot_minutes"] != 0:
        return error_response(422, "not_on_slot_grid"), None, None

    capacity = sum(t["capacity"] for t in restaurant["tables"] if t["id"] in table_ids)
    if party_size > capacity:
        return error_response(422, "party_exceeds_capacity"), None, None
    return None, starts_utc, ends_utc


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
            for f in ("starts_at_local",):
                if f in m and not isinstance(m[f], str):
                    return error_response(400, "malformed_request")
            # Handle table_id/table_ids types
            if "table_id" in m and not isinstance(m["table_id"], str):
                return error_response(400, "malformed_request")
            if "table_ids" in m and not isinstance(m["table_ids"], list):
                return error_response(400, "malformed_request")

        for m in moves:
            if "party_size" in m:
                ps = m["party_size"]
                if isinstance(ps, bool) or not isinstance(ps, int) or ps < 1:
                    return error_response(422, "validation_failed")

        resv = []
        for ref in refs:
            r = state.reservations.get(ref)
            if r is None or r["user_id"] != user_id:
                return error_response(404, "not_found")
            resv.append(r)
        if len({r["restaurant_id"] for r in resv}) != 1:
            return error_response(422, "validation_failed")

        restaurant = state.restaurants[resv[0]["restaurant_id"]]
        tz_name = restaurant["timezone"]
        now_utc = get_now_utc()
        cutoff = timedelta(minutes=restaurant["cancellation_cutoff_minutes"])
        results = []
        for m, r in zip(moves, resv):
            if r["status"] == "cancelled":
                return error_response(409, "reservation_cancelled")
            if now_utc >= parse_rfc3339(r["starts_at"]) - cutoff:
                return error_response(409, "cutoff_passed")

            # Handle table_id/table_ids in move
            if "table_id" in m or "table_ids" in m:
                err, new_table_ids = validate_table_ids(m, restaurant)
                if err:
                    status, code = err
                    return error_response(status, code)
                table_ids = new_table_ids
            else:
                table_ids = r.get("table_ids", [])

            local = m.get("starts_at_local", r["starts_at_local"])
            party = m.get("party_size", r["party_size"])
            err, starts_utc, ends_utc = validate_amendment(restaurant, table_ids, local, party)
            if err:
                return err
            results.append((table_ids, local, party, starts_utc, ends_utc))

        rid = restaurant["id"]
        old = {}
        for r in resv:
            for table_id in r.get("table_ids", []):
                old.setdefault((rid, table_id), []).append(
                    (parse_rfc3339(r["starts_at"]), parse_rfc3339(r["ends_at"])))
        remaining = {}
        for k, lst in state.occupancy.items():
            lst = list(lst)
            for iv in old.get(k, []):
                if iv in lst:
                    lst.remove(iv)
            remaining[k] = lst
        for table_ids, _l, _p, s0, e0 in results:
            for table_id in table_ids:
                for s1, e1 in remaining.get((rid, table_id), []):
                    if s0 < e1 and e0 > s1:
                        return error_response(409, "table_unavailable")
                remaining.setdefault((rid, table_id), []).append((s0, e0))

        # Check no overlap within resulting moves
        for i, (tids_a, _, _, s0a, e0a) in enumerate(results):
            for tids_b, _, _, s0b, e0b in results[i + 1:]:
                for tid_a in tids_a:
                    for tid_b in tids_b:
                        if tid_a == tid_b and s0a < e0b and s0b < e0a:
                            return error_response(409, "table_unavailable")

        for r, (table_ids, local, party, s0, e0) in zip(resv, results):
            r["table_ids"] = table_ids
            r["starts_at_local"] = local
            r["party_size"] = party
            r["starts_at"] = format_rfc3339(s0)
            r["ends_at"] = format_rfc3339(e0)
        state.occupancy = remaining
        response_body = {"reservations": [format_reservation_response(r, tz_name) for r in resv]}
        state.idempotency[idem_key] = {
            "method": "POST",
            "path": "/reservation-moves",
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
    Route("/reservations/{reference}", patch_reservation, methods=["PATCH"]),
    Route("/reservation-moves", reservation_moves, methods=["POST"]),
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
