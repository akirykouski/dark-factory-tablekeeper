# Stage 2 — numbered requirements and acceptance criteria

Source: the stage-2 specification (online booking and combined tables). Every stage-1
requirement (`spec/stage-1.md`, S1-01..S1-82) still applies unless amended here. Decisions
taken where the specification is silent are marked **Decision**.

## A. Combined tables — model

- **S2-01** Restaurant fixtures may carry `combinable: [[a,b],...]` (default `[]`): unordered
  pairs of that restaurant's table ids. **Decision:** reset rejects (422) a pair that is not
  exactly two distinct ids of that restaurant.
- **S2-02** Only listed pairs combine; never three or more; not transitive.
- **S2-03** A pair's capacity is the sum of its two tables' capacities.
- **S2-04** Seeded reservations are confirmed unless `status: "cancelled"`; they may hold
  `table_id` or `table_ids`. Cancelled seeds occupy nothing.
- **S2-05** `GET /restaurants/{id}` additionally returns `combinable` in fixture order and
  shape. **Decision.**

## B. Availability

- **S2-06** Every slot gains `available_options: [{table_ids, capacity}]`;
  `available_table_ids` is unchanged (singles only).
- **S2-07** Options = every single table and declared pair with capacity ≥ party_size and no
  overlapping confirmed reservation on any member. Singles first in fixture order, then
  pairs in `combinable` order; `table_ids` within a pair in `combinable` order.
- **S2-08** A pair is unavailable when either member is occupied by a single or by another
  pair booking.

## C. Reservations

- **S2-09** `POST /reservations` accepts `table_ids` (array of strings) instead of
  `table_id`. `table_id` alone still works and means a set of one. Both present → 422
  `validation_failed`; neither → 422 `validation_failed`.
- **S2-10** `table_ids` of the wrong type (not an array, or a non-string member) → 400
  `malformed_request`; empty array → 422 `validation_failed`.
- **S2-11** Duplicate id in the set → 422 `validation_failed` (checked before size).
- **S2-12** More than two ids → 422 `combination_not_allowed`.
- **S2-13** Unknown id or another restaurant's id → 404 `not_found` (before combination).
- **S2-14** A two-id set not declared (in either order) → 422 `combination_not_allowed`.
- **S2-15** A reversed declared pair is the same set; responses list it in declared order.
- **S2-16** `party_size` > summed capacity → 422 `party_exceeds_capacity`.
- **S2-17** Any member occupied for an overlapping interval → 409 `table_unavailable`.
- **S2-18** Rule order: types 400 → 422 field validation (incl. both/neither, empty,
  duplicates) → 404 restaurant/tables → 422 `combination_not_allowed` → 422
  `invalid_local_time` → 422 `outside_opening_hours` → 422 `not_on_slot_grid` → 422
  `party_exceeds_capacity` → 409 `table_unavailable`.
- **S2-19** Every reservation response carries `table_ids`; it carries `table_id` only when
  the set has exactly one member (key absent otherwise). Applies to create, replay, get,
  list, cancel, PATCH, moves and seeded reservations.
- **S2-20** A booking of a pair occupies both tables for its full duration; cancel frees
  both.
- **S2-21** `PATCH` accepts `table_ids` under the same rules (single ↔ pair changes allowed;
  both keys → 422). Failures change nothing.
- **S2-22** `POST /reservation-moves` items accept `table_ids`; no table may be in two
  overlapping resulting bookings (e.g. a pair and a single on a member).
- **S2-23** Concurrent requests are serializable; no read ever shows a double booking.
- **S2-24** Idempotent replays of pair bookings return the original body (with
  `table_ids`, no `table_id`).

## D. Upgrade

- **S2-25** A stage-2 service imports an export produced by the stage-1 service:
  logins, tokens, references, replays and occupancy all keep working; stage-1 reservations
  read with `table_ids: [table_id]` and `table_id`.
- **S2-26** A browser signed in before an export/import keeps its session (token valid);
  a booking whose response was lost before export is retryable after import with the same
  key/body and the UI shows the original reference.

## E. UI — general (product bar)

- **S2-27** Routes `/`, `/signup`, `/login`, `/lookup` return HTML (200, `text/html`);
  other screens reachable through the UI. All assets (scripts, styles, fonts) served by
  the service; no external URLs.
- **S2-28** One visual system defined once (CSS variables for type scale, spacing, colour);
  warm hospitality character; clear primary action per screen; human labels (restaurant
  names, table labels) instead of ids; consistent navigation on every route.
- **S2-29** At 375 px and 1280 px: no horizontal page scroll; visible input labels; visible
  keyboard focus; sufficient contrast; touch targets ≥ 44 px.
- **S2-30** Available, unavailable, selected, loading, success, refused and uncertain
  states are visually distinct; considered empty, loading and error states.

## F. UI — auth

- **S2-31** `signup-email`, `signup-password`, `signup-display-name`, `signup-submit`;
  `login-email`, `login-password`, `login-submit`; `auth-error` present only when there is
  an error (e.g. wrong password, taken email, short password).
- **S2-32** When signed in, `current-user` is visible on every screen and contains the
  display name; `logout-button` signs out (current-user disappears).
- **S2-33** **Decision:** session token kept in `localStorage`, so it survives navigation
  between routes.

## G. UI — search and grid (`/`)

- **S2-34** `restaurant-select` (option values = restaurant ids, labels = names),
  `date-input` (value `YYYY-MM-DD`), `party-size-input` (number), `search-button`,
  `availability-grid`.
- **S2-35** One cell `slot-{table_id}-{HH:MM}` per table per slot, with
  `data-available="true"` exactly when the table is in that slot's `available_table_ids`
  for the searched party size, otherwise `"false"`.
- **S2-36** Combination cells `slot-{a}+{b}-{HH:MM}` (declared order). **Decision:** shown
  for every declared pair whose summed capacity ≥ the searched party size, in every slot,
  `data-available` true exactly when the pair is in that slot's `available_options`.
- **S2-37** `no-slots` shown instead of the grid when the day has no slots.
- **S2-38** Clicking an available cell opens the booking form for that table set and slot;
  clicking an unavailable cell does nothing. Signed out: clicking an available cell shows
  `auth-error` (**Decision**: stays on the page).
- **S2-39** Out-of-order responses: when search A starts before B and finishes after it,
  grid, labels and form describe B. A late response never restores A.

## H. UI — booking form and confirmation

- **S2-40** `booking-form`, `booking-summary` (contains every table label of the selection
  and the local start time `HH:MM`), `booking-party-size` (pre-filled from the search),
  `booking-submit`, `booking-error` (only when the booking fails).
- **S2-41** Success shows `confirmation` with `confirmation-reference` (text exactly the
  reference), `confirmation-details` (restaurant name, table label(s), local start time),
  `confirmation-tables` (every table label). The form stays on screen.
- **S2-42** Re-submitting the unchanged form sends the same key and body → same
  reference, no `booking-error`, no second booking. Changing any field makes the next
  submit a new request (new key).
- **S2-43** 409 `table_unavailable`: show `booking-error`, refresh availability (the cell
  now reads false), keep the form and its inputs, no confirmation for that attempt.
- **S2-44** Lost response (network failure, even after the server committed): show
  non-empty `booking-uncertain`, no `booking-error`, no new confirmation. Retrying the
  unchanged form reuses key + body; success removes the uncertainty/error elements and
  shows the original reference. A confirmed rejection on retry shows `booking-error`.
- **S2-45** The UI never shows success the server did not confirm.

## I. UI — lookup (`/lookup`)

- **S2-46** `lookup-reference-input`, `lookup-submit`; found → `reservation-detail`,
  `reservation-status` (exactly `confirmed`/`cancelled`), `reservation-tables` (every table
  label), `reservation-cancel-button` (absent once cancelled); not found or refused cancel
  → `reservation-error`.
- **S2-47** Lookup requires the signed-in owner (API rule); signed out shows
  `reservation-error` or the login prompt.
- **S2-48** After cancel, status reads `cancelled` and the cancel button disappears; a
  refused cancel (cutoff) shows `reservation-error` and status stays `confirmed`.
