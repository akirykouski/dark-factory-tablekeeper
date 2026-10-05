# Stage 1 — numbered requirements and acceptance criteria

Source: the stage-1 specification (reservations API). Every normative sentence is listed
below as a requirement `S1-xx` with a one-line acceptance criterion (AC). Where the
specification is silent, the decision taken is written as **Decision** and is binding for
builders and tests alike.

## A. Delivery and runtime

- **S1-01** Deliver `stage-1/Dockerfile`, `stage-1/RUN.md` and source; RUN.md holds one command
  that builds and starts the service from that folder with no manual setup.
  AC: `docker build` + `docker run -e PORT=p -p p:p` from the folder serves `/health` 200.
- **S1-02** Listen on `0.0.0.0:$PORT`, default 8080. AC: container started with `-e PORT=9123`
  answers on 9123; without PORT answers on 8080.
- **S1-03** No outbound network at run time; all assets in the image. AC: runs on a Docker
  `--internal` network.
- **S1-04** `GET /health` → 200 `{"status":"ok"}` within 60 s of start. AC: exact body.
- **S1-05** Works within 2 vCPU / 2 GiB, 50 concurrent in-flight requests, 5 s per request
  (10 s for reset). AC: bursts of 50 complete with no 5xx and no timeouts.
- **S1-06** No request ever yields 5xx, including malformed input and concurrent load.
  AC: every test asserts status < 500.

## B. Conventions

- **S1-07** Responses are `application/json; charset=utf-8` (except 204). AC: header check.
- **S1-08** Response timestamps are RFC 3339 with explicit numeric offset, second precision
  (`2026-09-24T19:00:00+02:00`; `created_at` uses `+00:00`). **Decision:** never `Z`, never
  fractional seconds. `starts_at` and `ends_at` carry the **restaurant's local offset** for that
  instant (the specification's examples and §9 "its local `ends_at` will read 02:00");
  `created_at` is UTC. AC: regex `^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$`.
- **S1-09** Unknown body fields and unknown query parameters are ignored. AC: extra fields
  do not change the outcome.
- **S1-10** IDs are opaque strings of at most 64 characters, including fixture IDs.
  **Decision:** a reset fixture with any id longer than 64 characters → 422
  `validation_failed` and state unchanged; a path id longer than 64 → 404 `not_found`.

## C. Errors (§5)

- **S1-11** Every 4xx/5xx has body `{"error":{"code":<str>,"message":<str>}}` (exactly those
  two keys inside `error`, `error` the only top-level key). AC: shape check on every error.
- **S1-12** Unparseable body, or a body that is not a JSON object → 400 `malformed_request`.
- **S1-13** A field with the wrong JSON type → 400 `malformed_request`, except where an
  endpoint says otherwise (`party_size` any invalid value → 422; `moves` shape → 422).
  **Decision:** `null` counts as a wrong type (400) for every field except `party_size`
  (422). A missing required field → 422 `validation_failed`.
- **S1-14** Correct type with invalid format/range → 422 `validation_failed` unless a more
  specific code applies.
- **S1-15** Integer query parameters must be plain decimal digits; `1e9`, `4.0`, `+4`, `-1`,
  ` 4`, `4 `, `` → 422.
- **S1-16** `Idempotency-Key` absent or empty → 400 `missing_idempotency_key`; longer than 255
  characters → 422 `validation_failed`. Exactly 1 and 255 characters are valid.
- **S1-17** Error precedence for authenticated writes (decision, applied uniformly):
  401 auth → 400 malformed body / not an object → 400 missing key → 422 key length →
  idempotency resolution (replay 200 / 409 reuse) → 400 field types → 422 missing/format →
  404 lookups → rule checks (endpoint order below).

## D. Reset, fixture, model (§3.3, §4)

- **S1-18** `POST /_test/reset` with a fixture → 204, empty body; afterwards only fixture state
  is visible (users, restaurants, reservations, tokens and idempotency records all cleared).
  Repeatable. No auth. AC: old token → 401 after reset; old reservation → gone.
- **S1-19** Fixture: `users[]` (id, email, password, display_name), `restaurants[]` (id, name,
  timezone, slot_minutes, reservation_duration_minutes, cancellation_cutoff_minutes,
  opening_hours[{weekday,opens,closes}], tables[{id,label,capacity}]), `reservations[]`
  (POST body fields + id, reference, user_id). Missing top-level lists default to `[]`.
- **S1-20** Seeded users log in immediately with the fixture password.
- **S1-21** Seeded reservations are confirmed, owned by `user_id`, visible in that user's
  list, occupy their table, keep the given `id` and `reference`. **Decision:** seeded
  `created_at` is used if supplied, else the reset time.
- **S1-22** Weekday keys `mon..sun`; `opens`/`closes` local `HH:MM`; a weekday without an
  entry is closed.
- **S1-23** Past dates are bookable (not rejected for being past); cutoff rules still apply.

## E. Authentication (§6)

- **S1-24** `POST /auth/signup {email,password,display_name}` → 201
  `{user_id, display_name, token}` (exact key set).
- **S1-25** `POST /auth/login {email,password}` → 200 `{user_id, display_name, token}`.
- **S1-26** Signup with a registered email → 409 `email_taken`. **Decision:** email
  comparison is case-insensitive; seeded emails count as registered.
- **S1-27** Password shorter than 8 characters → 422; exactly 8 accepted. Length counts
  Unicode characters.
- **S1-28** Email not `local@domain` (exactly one `@`, non-empty local and domain, no
  whitespace anywhere) → 422. Leading/trailing whitespace → 422.
- **S1-29** `display_name` must be a non-empty string → else 422 (missing) / 400 (wrong type).
- **S1-30** Wrong password or unknown email on login → 401 `unauthenticated`.
- **S1-31** All endpoints except `/health`, `/_test/*`, `/auth/*`, `GET /restaurants`,
  `GET /restaurants/{id}`, `GET /availability` require `Authorization: Bearer <token>`;
  missing, malformed (`Basic x`, `Bearer` with no token) or unknown → 401.
- **S1-32** Tokens never expire; multiple tokens per account all valid concurrently.
- **S1-33** Passwords stored with a slow password hash (bcrypt/scrypt/Argon2/PBKDF2); never
  plaintext. AC: export state contains no plaintext password.

## F. Idempotency (§7)

- **S1-34** Required on `POST /reservations` and `POST /reservation-moves`.
- **S1-35** Scope is (user, key). Same key by different users: independent.
- **S1-36** Replay = same user + method + path + JSON-equal body (key order/whitespace
  irrelevant) → 200 with body JSON-equal to the original 201 body.
- **S1-37** Same key, same body, different path → not a replay; processed normally.
- **S1-38** Same key + different body (after original success) → 409
  `idempotency_key_reuse`, even if the new body is invalid.
- **S1-39** Key whose original request failed with 4xx is free: next use is a first use.
- **S1-40** Concurrent identical requests with an unused key: exactly one 201, the rest 200
  with identical body; one effect.
- **S1-41** Replay returns the original response even after the resource is changed or
  cancelled, and makes no state change.

## G. Restaurants and availability (§8)

- **S1-42** `GET /restaurants` (public) → `{"restaurants":[{id,name,timezone}]}` in fixture
  order, exactly those keys.
- **S1-43** `GET /restaurants/{id}` (public) → `{id,name,timezone,slot_minutes,
  reservation_duration_minutes,cancellation_cutoff_minutes,opening_hours,tables}` in fixture
  shape; unknown → 404.
- **S1-44** `GET /availability?restaurant_id&date&party_size` (public). Any missing → 422.
  `date` must be a real `YYYY-MM-DD` date (2026-02-30, 2026-9-24, `20260924` → 422).
  `party_size` digits only and ≥ 1 (0 → 422). Unknown restaurant → 404. Precedence:
  422 (missing/format) before 404.
- **S1-45** Response `{restaurant_id, date, timezone, slots}`; each slot exactly
  `{starts_at_local, starts_at, available_table_ids}`.
- **S1-46** Slots for every `slot_minutes` step from `opens` while slot instant +
  duration ≤ `closes` instant; local times that do not exist are skipped; repeated local
  times appear once (first occurrence).
- **S1-47** `available_table_ids`: tables with capacity ≥ party_size and no overlapping
  confirmed reservation, fixture order. Slots with none still appear with `[]`. Party
  larger than every table → every slot `[]`.
- **S1-48** Closed day → `"slots": []`.
- **S1-49** Occupancy is half-open `[start, start+duration)` in absolute time: a booking
  ending at 20:30 does not block 20:30; a cancelled booking blocks nothing.

## H. Reservations (§8)

- **S1-50** `POST /reservations {restaurant_id, table_id, starts_at_local, party_size}` →
  201 with exactly `{reservation_id, reference, restaurant_id, table_id, party_size, status,
  starts_at_local, starts_at, ends_at, created_at}`; status `confirmed`.
- **S1-51** `reference`: 6–12 chars `[A-Z0-9]`, unique across all reservations, immutable.
- **S1-52** `starts_at_local` must match `^\d{4}-\d\d-\d\dT\d\d:\d\d$` exactly and be a real
  date/time; anything else (offset, `Z`, seconds, spaces, trailing newline) → 422
  `validation_failed`. Non-string → 400.
- **S1-53** `party_size` must be an integer ≥ 1; strings, booleans, floats (incl. `4.0`),
  null, 0, negatives → 422 `validation_failed`.
- **S1-54** Rule order after field validation: 404 restaurant → 404 table (unknown or other
  restaurant's) → 422 `invalid_local_time` (nonexistent local time) → 422
  `outside_opening_hours` (closed day, before opens, or end after closes) → 422
  `not_on_slot_grid` (minutes since opens not a multiple of slot_minutes) → 422
  `party_exceeds_capacity` → 409 `table_unavailable`.
- **S1-55** A rejected create changes nothing (no reservation, no occupancy, key free).
- **S1-56** Two confirmed reservations never overlap on one table, including under a
  concurrent burst (AC: 30 concurrent creates for the same table/slot with different keys →
  exactly one 201, rest 409).
- **S1-57** `GET /reservations` → `{"reservations":[...]}` caller's only, `starts_at` desc
  (**Decision:** ties by `created_at` desc then reference), confirmed and cancelled.
- **S1-58** `GET /reservations/{reference}` → the reservation; another user's or unknown →
  404.
- **S1-59** `POST /reservations/{reference}/cancel` → 200 full reservation with status
  `cancelled`; frees the table immediately. Already cancelled → 200 current state (checked
  before cutoff). `now ≥ starts_at − cutoff` → 409 `cutoff_passed`. Not the caller's → 404.
- **S1-60** `PATCH /reservations/{reference}` with any subset of `table_id`,
  `starts_at_local`, `party_size` → 200 full reservation. Order: 404 → 409
  `reservation_cancelled` → 409 `cutoff_passed` (against current start) → field and rule
  validation as S1-52..54 on the resulting values → 409 `table_unavailable` (ignoring the
  booking's own occupancy). `reference`, `reservation_id`, `created_at` unchanged.
- **S1-61** A failed PATCH changes nothing (read back identical, old slot still occupied).
  A successful PATCH frees the old slot and takes the new one atomically.
- **S1-62** **Decision:** an empty PATCH or one setting current values succeeds (200,
  unchanged) but still requires a confirmed booking outside its cutoff.

## I. Time and DST (§9)

- **S1-63** Local times use the restaurant's IANA timezone; offsets follow IANA for the date
  (Berlin +02:00 in September, +01:00 in December).
- **S1-64** Spring forward: skipped local times never appear in availability; booking one →
  422 `invalid_local_time` (Berlin 2026-03-29 02:00–02:59, New York 2026-03-08 02:00–02:59).
- **S1-65** Fall back: repeated local times resolve to the first occurrence; they appear once
  in availability (Berlin 2026-10-25 02:xx at +02:00, New York 2026-11-01 01:xx at −04:00).
- **S1-66** Duration is absolute: Berlin 2026-10-25 01:30 + 90 min → `ends_at`
  `2026-10-25T02:00:00+01:00`; New York 2026-11-01 01:30 → `2026-11-01T02:00:00-05:00`.

## J. Export / import (§10)

- **S1-67** `GET /_test/export` → 200 `{"track":"tablekeeper","format_version":1,"state":{...}}`;
  atomic read-only snapshot (later writes do not change an already returned export).
- **S1-68** `POST /_test/import <export>` → 204; atomically replaces all state. Repeating
  it restores the same state with no duplicates.
- **S1-69** Invalid JSON → 400; missing `track`/`format_version`/`state`, wrong track, wrong
  version, or invalid state → 422 `validation_failed` and destination unchanged.
- **S1-70** Preserved: users + hashed passwords (login works), bearer tokens, restaurants,
  reservations (ids, references, statuses, timestamps), completed idempotency records with
  original responses (replay → 200 identical; different body → 409), move receipts. Failed
  keys stay reusable. Previous destination data and tokens are removed.
- **S1-71** Reset after import clears imported state.

## K. Atomic reservation moves (§11)

- **S1-72** `POST /reservation-moves` needs auth (401) and an idempotency key (§7).
- **S1-73** Body `{"moves":[...]}`: 1..8 objects, each with string `reference`, references
  distinct; otherwise 422 `validation_failed` (including `moves` missing / not an array /
  empty / 9 items / item not an object / reference not a string / duplicates).
  **Decision:** within an item, `table_id`/`starts_at_local` of the wrong type → 400,
  `party_size` invalid → 422, like PATCH.
- **S1-74** Unknown or another owner's reference → 404; bookings in different restaurants
  → 422 `validation_failed`.
- **S1-75** Per item, omitted fields keep current values; unknown fields ignored.
- **S1-76** Item checks in input order: 409 `reservation_cancelled`, then 409 `cutoff_passed`,
  then the ordinary amendment validation codes; the first failing item decides.
  **Decision:** 404 for any reference (input order) precedes the restaurant check, which
  precedes per-item checks.
- **S1-77** Occupancy is checked on the resulting set: overlap among resulting bookings or
  with any unlisted booking → 409 `table_unavailable`. Swaps (A→B's table, B→A's table)
  succeed. Unchanged listed bookings keep their occupancy.
- **S1-78** All-or-nothing: on any failure no reservation, occupancy or key changes.
- **S1-79** Success → 201 `{"reservations":[...]}` in input order including unchanged items;
  identity, owner, `created_at` never change.
- **S1-80** Replays → 200 original body, even after later amendments/cancellations.
- **S1-81** No-op moves retain all values.
- **S1-82** Export/import preserves move receipts and resulting bookings.
