# Stage 4 — numbered requirements and acceptance criteria

Source: the stage-4 specification (seating changes and recurring amendments). All earlier
requirements (`spec/stage-1.md` … `spec/stage-3.md`) still apply. Decisions are marked
**Decision**. As in stage 3, every invalid field value on the new write endpoints (any JSON
type) is 422 `validation_failed`; an unparseable or non-object body is 400.

## A. Restaurant revision

- **S4-01** Each restaurant has a revision: 0 after reset; +1 for each successful new
  booking, real amendment, cancellation (not a repeated one), policy publication, plan
  application, reservation-moves batch with at least one real change (once per batch),
  series adoption (once for the whole adoption) and series amendment with at least one real
  change (once). No-ops, failures, previews and replays never increment it. Exported and
  imported with the state. **Decision:** state imported from an earlier stage starts at 0.

## B. Replan preview

- **S4-02** `POST /restaurants/{id}/replans` — no token 401; unknown restaurant 404;
  non-manager 403 `forbidden`; idempotency key required (stage-1 replay rules).
- **S4-03** Body `{table_id, from, to}`: `table_id` string; `from`/`to` RFC 3339 instants with
  an explicit offset (`Z` or `±HH:MM`; no naive times, no date-only); `from < to`; anything
  else 422 `validation_failed`. Unknown table (of that restaurant) → 404 `not_found` (after
  validation).
- **S4-04** The closure is `[from, to)`. Considered bookings = every confirmed booking of
  that restaurant whose occupancy overlaps the closure (on any table). Other bookings are
  fixed.
- **S4-05** More than 6 tables, more than 4 declared pairs, or more than 6 considered
  bookings → 422 `planning_limit`.
- **S4-06** Each considered booking keeps reference, owner, party size, start, end and
  accepted terms, and is assigned one single table or one declared pair with capacity ≥
  party size under **its own accepted terms' capacities**, with no conflict against fixed
  bookings, other assignments, previously applied closures or the proposed closure (a
  closed table cannot be used by any considered booking). Diner cutoffs do not apply.
- **S4-07** Among feasible plans minimise lexicographically: (1) number of bookings whose
  table set changes, (2) total unused seats (capacity − party size, summed), (3) the vector
  of option ranks in ascending reference order (singles ranked first in fixture order, then
  pairs in declared order, from 0).
- **S4-08** 201 `{plan_id, restaurant_revision, closure: {table_id, from, to}, assignments:
  [{reference, table_ids, changed}], moved_count, unused_seats}`; assignments for every
  considered booking in ascending reference order; `restaurant_revision` = current revision;
  `table_ids` in declared order. **Decision:** `closure.from`/`to` echo the request strings.
- **S4-09** Preview stores only the plan: no closure, occupancy, reservation revision,
  history or restaurant revision change.
- **S4-10** No feasible plan → 409 `no_feasible_plan`, nothing stored (key stays free).

## C. Plan application

- **S4-11** `POST /restaurants/{id}/replans/{plan_id}/apply`, body `{}`: manager +
  idempotency key (401/403/400 as above). Unknown plan or a plan of another restaurant →
  404.
- **S4-12** Order: 404 → 409 `plan_already_applied` (applied under another key) → 409
  `stale_plan` (restaurant revision differs from the plan's) → apply. Replay of the
  successful key → 200 original body, even after later changes.
- **S4-13** 201 `{plan_id, restaurant_revision (new), reservations: [...]}` — every considered
  booking (ordinary reservation response) in ascending reference order.
- **S4-14** Atomic: closure + all assignments recorded together. Each moved booking: revision
  +1, one `reassigned` history entry with `changes: [{field: "table_ids", from, to}]` and
  `plan_id`, same accepted terms and times. Unmoved bookings unchanged. Restaurant revision
  +1 once.
- **S4-15** Applied closures: tables excluded (singles and pairs) from availability during
  `[from,to)`; creates/amendments/moves onto them → 409 `table_unavailable`; explanations
  report `no_overlap: false`.
- **S4-16** Moved series occurrences keep exception flags, scheduled dates, identities and
  terms; each affected series revision +1 once per application if a member moved.
- **S4-17** Concurrent applications never leave partially moved bookings; a closure or
  booking at another restaurant does not invalidate a plan.

## D. Series amendment

- **S4-18** `POST /series/{series_id}/amend` — no token 401; unknown or another owner's series
  404; idempotency key required; replay → 200 original body even after later edits.
- **S4-19** Body `{expected_revision, from_index, local_time}`: positive integer; integer
  0..count−1; string exactly `HH:MM` 00:00..23:59. Invalid → 422. Unknown fields ignored.
- **S4-20** `expected_revision` ≠ series revision → 409 `stale_revision`, before any
  occurrence check.
- **S4-21** Eligible occurrences: index ≥ from_index, not cancelled, not exception. Each moves
  to `local_time` on its original scheduled local date (anchor date + index·interval·7 days),
  keeping reference, owner, party size and current table selection. Identical result → no-op
  (terms kept).
- **S4-22** Each real change: old accepted cutoff, then the resulting date's policy (DST,
  hours, grid, capacity); then occupancy against unchanged occurrences, other bookings and
  applied closures. Non-occupancy errors decided in index order; otherwise 409
  `table_unavailable`. Failure changes nothing (histories, keys, revisions).
- **S4-23** 201 = current series response. Each changed occurrence: one `changed` history
  entry, revision +1. Series revision and restaurant revision +1 once if anything changed.
  Not exceptions. All-no-op or empty eligible set → 201, no revision change.
- **S4-24** Concurrent amendments with the same expected revision: at most one real change.

## E. Upgrade

- **S4-25** Imports exports of stages 1–3 (including series with moved and cancelled
  occurrences); replans and series amendments work on imported data; earlier receipts,
  histories and retries stay valid. Stage-4 export/import keeps closures, plans, applied
  receipts and restaurant revisions.
