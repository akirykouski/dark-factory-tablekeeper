"""Tablekeeper Stage 1: Reservations API."""
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

    return {
        "reservation_id": res["reservation_id"],
        "reference": res["reference"],
        "restaurant_id": res["restaurant_id"],
        "table_id": res["table_id"],
        "party_size": res["party_size"],
        "status": res["status"],
        "starts_at_local": res["starts_at_local"],
        "starts_at": format_rfc3339(starts_utc.astimezone(tz)),
        "ends_at": format_rfc3339(ends_utc.astimezone(tz)),
        "created_at": res["created_at"],
    }


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

    return JSONResponse(state.restaurants[rid])


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

            slots.append({
                "starts_at_local": f"{date}T{slot_h:02d}:{slot_m:02d}",
                "starts_at": format_rfc3339(slot_utc.astimezone(tz)),
                "available_table_ids": available,
            })

            current_min += slot_minutes

    return JSONResponse({
        "restaurant_id": restaurant_id,
        "date": date,
        "timezone": tz_name,
        "slots": slots,
    })


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
        idempotency_key = (user_id, key)
        if idempotency_key in state.idempotency:
            recorded = state.idempotency[idempotency_key]
            current_json = json.dumps(body, sort_keys=True, separators=(',', ':'))
            if current_json == recorded["body_json"]:
                return JSONResponse(recorded["response"], status_code=200)
            else:
                return error_response(409, "idempotency_key_reuse")

        # Validate and create reservation
        for field in ["restaurant_id", "table_id", "starts_at_local", "party_size"]:
            if field not in body:
                return error_response(422, "validation_failed")

        restaurant_id = body["restaurant_id"]
        table_id = body["table_id"]
        starts_at_local = body["starts_at_local"]
        party_size = body["party_size"]

        if not isinstance(restaurant_id, str):
            return error_response(400, "malformed_request")
        if not isinstance(table_id, str):
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

        table = None
        for t in restaurant["tables"]:
            if t["id"] == table_id:
                table = t
                break

        if not table:
            return error_response(404, "not_found")

        tz_name = restaurant["timezone"]

        # Validate starts_at_local format and values before parsing
        try:
            date_str, time_str = starts_at_local.split('T')
            year, month, day = map(int, date_str.split('-'))
            hour, minute = map(int, time_str.split(':'))
            if hour > 23 or minute > 59:
                return error_response(422, "validation_failed")
            # Try to construct datetime - will raise ValueError if invalid date
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

        if party_size > table["capacity"]:
            return error_response(422, "party_exceeds_capacity")

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
            "table_id": table_id,
            "party_size": party_size,
            "status": "confirmed",
            "starts_at_local": starts_at_local,
            "starts_at": format_rfc3339(starts_utc),
            "ends_at": format_rfc3339(ends_utc),
            "created_at": format_rfc3339(now_utc),
            "user_id": user_id,
        }

        state.reservations[reference] = reservation
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

    key = (res["restaurant_id"], res["table_id"])
    starts = parse_rfc3339(res["starts_at"])
    ends = parse_rfc3339(res["ends_at"])
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
    table_id = res["table_id"]
    starts_at_local = res["starts_at_local"]
    party_size = res["party_size"]

    if "table_id" in body:
        table_id = body["table_id"]
    if "starts_at_local" in body:
        starts_at_local = body["starts_at_local"]
    if "party_size" in body:
        party_size = body["party_size"]

    if "table_id" in body and not isinstance(table_id, str):
        return error_response(400, "malformed_request")
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

    table = None
    for t in restaurant["tables"]:
        if t["id"] == table_id:
            table = t
            break

    if not table:
        return error_response(404, "not_found")

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

    if party_size > table["capacity"]:
        return error_response(422, "party_exceeds_capacity")

    key = (restaurant_id, table_id)
    old_key = (res["restaurant_id"], res["table_id"])
    old_starts = parse_rfc3339(res["starts_at"])
    old_ends = parse_rfc3339(res["ends_at"])

    if key in state.occupancy:
        for s, e in state.occupancy[key]:
            if s == old_starts and e == old_ends and key == old_key:
                continue
            if starts_utc < e and ends_utc > s:
                return error_response(409, "table_unavailable")

    res["table_id"] = table_id
    res["starts_at_local"] = starts_at_local
    res["starts_at"] = format_rfc3339(starts_utc)
    res["ends_at"] = format_rfc3339(ends_utc)
    res["party_size"] = party_size

    if old_key in state.occupancy:
        state.occupancy[old_key] = [(s, e) for s, e in state.occupancy[old_key] if not (s == old_starts and e == old_ends)]

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
    for u in users:
        if not _valid_id(u.get("id")):
            return None
        email, password = u.get("email"), u.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            return None
        if not isinstance(u.get("display_name"), str):
            return None
        password_hash, password_salt = hash_password(password)
        new_users[u["id"]] = {
            "id": u["id"],
            "email_lower": email.lower(),
            "password_hash": password_hash,
            "password_salt": password_salt,
            "display_name": u["display_name"],
        }

    new_restaurants = {}
    for r in restaurants:
        if not _valid_id(r.get("id")):
            return None
        for t in r.get("tables") or []:
            if not isinstance(t, dict) or not _valid_id(t.get("id")):
                return None
        new_restaurants[r["id"]] = r

    new_res = {}
    new_occ = {}
    now = get_now_utc()
    for rd in reservations:
        rid, ref, uid = rd.get("id"), rd.get("reference"), rd.get("user_id")
        if not (_valid_id(rid) and _valid_id(ref) and _valid_id(uid)):
            return None
        if ref in new_res:
            return None
        restaurant = new_restaurants.get(rd.get("restaurant_id"))
        if restaurant is None:
            return None
        table_id = rd.get("table_id")
        if not any(isinstance(t, dict) and t.get("id") == table_id
                   for t in restaurant.get("tables") or []):
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
        new_res[ref] = {
            "reservation_id": rid,
            "reference": ref,
            "restaurant_id": rd["restaurant_id"],
            "table_id": table_id,
            "party_size": party,
            "status": "confirmed",
            "starts_at_local": rd["starts_at_local"],
            "starts_at": format_rfc3339(starts_utc),
            "ends_at": format_rfc3339(ends_utc),
            "created_at": created,
            "user_id": uid,
        }
        new_occ.setdefault((rd["restaurant_id"], table_id), []).append((starts_utc, ends_utc))

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
    export_data = {
        "track": "tablekeeper",
        "format_version": 1,
        "state": {
            "users": list(state.users.values()),
            "tokens": state.tokens,
            "restaurants": list(state.restaurants.values()),
            "reservations": list(state.reservations.values()),
            "idempotency": state.idempotency,
            "occupancy": {str(k): v for k, v in state.occupancy.items()},
        }
    }
    return JSONResponse(export_data)


async def import_(request: Request) -> Response:
    try:
        body = await request.json()
    except:
        return error_response(400, "malformed_request")

    if not isinstance(body, dict):
        return error_response(400, "malformed_request")

    if body.get("track") != "tablekeeper":
        return error_response(422, "validation_failed")
    if body.get("format_version") != 1:
        return error_response(422, "validation_failed")
    if "state" not in body:
        return error_response(422, "validation_failed")

    state.users = {u["id"]: u for u in body["state"].get("users", [])}
    state.tokens = body["state"].get("tokens", {})
    state.restaurants = {r["id"]: r for r in body["state"].get("restaurants", [])}
    state.reservations = {r["reference"]: r for r in body["state"].get("reservations", [])}
    state.idempotency = body["state"].get("idempotency", {})

    state.occupancy.clear()
    for res in body["state"].get("reservations", []):
        if res.get("status") == "confirmed":
            key = (res["restaurant_id"], res["table_id"])
            starts = parse_rfc3339(res["starts_at"])
            ends = parse_rfc3339(res["ends_at"])
            if key not in state.occupancy:
                state.occupancy[key] = []
            state.occupancy[key].append((starts, ends))

    return Response(status_code=204)


async def reservation_moves_placeholder(request: Request) -> JSONResponse:
    """POST /reservation-moves requires auth. Return 4xx, never 5xx."""
    user_id, auth_err = await require_auth(request)
    if auth_err:
        return auth_err

    key = request.headers.get("idempotency-key", "")
    if not key:
        return error_response(400, "missing_idempotency_key")

    # Return 422 for now (not implemented)
    return error_response(422, "validation_failed")


async def not_found(request: Request) -> JSONResponse:
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
    Route("/reservation-moves", reservation_moves_placeholder, methods=["POST"]),
    Route("/{path:path}", not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
