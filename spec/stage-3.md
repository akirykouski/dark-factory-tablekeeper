# Stage 3 — numbered requirements and acceptance criteria

Source: the stage-3 specification (booking policies, history, recurring reservations).
All stage-1 and stage-2 requirements (`spec/stage-1.md`, `spec/stage-2.md`) still apply.
Decisions taken where the specification is silent are marked **Decision**.

**Decision (input errors on the new write endpoints):** for `POST /restaurants/{id}/policies`,
`POST /series` and the `expected_revision` field, any invalid field value — wrong JSON type
included (booleans, strings for integers, floats, null) — is 422 `validation_failed`. A body
that does not parse or is not a JSON object stays 400 `malformed_request`.

## A. Availability explanations

- **S3-01** `explain` query parameter is optional; only the exact value `true` is accepted;
  `false`, `1`, `True`, `TRUE`, empty string, ` true` → 422 `validation_failed`.
- **S3-02** Without `explain`, slots keep exactly the stage-2 shape (no explanation fields).
- **S3-03** With `explain=true` every slot gains `explain: [...]`: one entry per table of the
  restaurant, exactly once, fixture order; entry keys exactly `{table_id, policy_version,
  available, rules}`; `rules` exactly `[{rule:"capacity",holds}, {rule:"no_overlap",holds}]`
  in that order, each `{rule, holds}` only.
- **S3-04** `capacity` holds iff party_size ≤ the table's capacity under the selected policy;
  `no_overlap` holds iff no confirmed reservation on that table overlaps the slot interval
  (slot duration = selected policy's duration).
- **S3-05** `available` = both hold; the available table ids are exactly
  `available_table_ids`, same order.
- **S3-06** Closed day → `slots: []`; slot with no available table still has a full `explain`.
- **S3-07** `policy_version` in each entry = the policy selected for the searched date.

## B. Reservation history

- **S3-08** `GET /reservations/{reference}/history` → `{reference, entries}`; owner only;
  another user, unknown reference, or no token at all → 404 `not_found` (never 401).
- **S3-09** Entry keys exactly `{seq, at, event, changes, revision, accepted_terms}`;
  `seq` starts at 1 and increments by 1; entries in `seq` order, `at` non-decreasing,
  RFC 3339 with offset. **Decision:** `at` is rendered in the restaurant's timezone.
- **S3-10** `created` lists `table_id` (or `table_ids` for a pair), `starts_at_local`,
  `party_size`, each with `from: null`, in that order.
- **S3-11** `changed` lists only fields that changed, order `table_id`/`table_ids`,
  `starts_at_local`, `party_size`. A PATCH to identical values records nothing.
- **S3-12** `cancelled` has `changes: []`; nothing follows it; a repeated cancel adds nothing.
- **S3-13** Replays record nothing.
- **S3-14** Pair history: creation of a pair records `table_ids` from null to the pair
  (declared order). A change involving a pair before or after records `table_ids` with full
  before/after lists. Single → single records `table_id`. A reversed input pair is the same
  set (no change).
- **S3-15** **Decision:** seeded reservations and reservations imported from an earlier
  stage's export with no history get a synthesized `created` entry (current fields, `at` =
  `created_at`, revision 1, policy-0 terms); if already cancelled, a `cancelled` entry
  follows and the revision is 2.

## C. Policies

- **S3-16** Fixture restaurants may carry `manager_user_ids` (default `[]`); restaurant
  detail returns the original fixture configuration (incl. `manager_user_ids`, `combinable`).
- **S3-17** `POST /restaurants/{id}/policies`: no token → 401; unknown restaurant → 404;
  authenticated non-manager → 403 `forbidden`; idempotency key required (stage-1 rules,
  replay 200 identical, different body 409, failed key reusable).
- **S3-18** Body = complete policy, all required: `effective_from` (real `YYYY-MM-DD`),
  `slot_minutes` and `reservation_duration_minutes` integers 1..1440,
  `cancellation_cutoff_minutes` integer 0..10080, `opening_hours` (stage-1 entry rules: weekday
  in mon..sun, `HH:MM` 00:00..23:59, closes > opens, no duplicate weekdays; empty list allowed),
  `capacities` object with exactly the restaurant's table ids, integers 1..100. Anything else
  → 422 `validation_failed`, no version, no state change. Unknown fields ignored.
- **S3-19** 201 body = the policy fields (`effective_from`, `slot_minutes`,
  `reservation_duration_minutes`, `cancellation_cutoff_minutes`, `opening_hours`,
  `capacities`) + `policy_version` (1, 2, … per restaurant). Failures and replays allocate no
  version.
- **S3-20** `GET /restaurants/{id}/policies` is public → `{"policies":[...]}` in publication
  order, same shape as the 201 body, policy 0 omitted; unknown restaurant → 404.
- **S3-21** Selection for a local date: greatest `effective_from` ≤ date; ties → greatest
  `policy_version`; none → policy 0 (fixture rules).
- **S3-22** Availability and booking decisions (grid, hours, duration, capacities, options
  capacity) use the selected policy for the date; restaurant detail stays original.
- **S3-23** Publication never changes existing bookings, their end times, terms or history.

## D. Accepted terms and revisions

- **S3-24** Every reservation response gains `revision` (1 at creation) and `accepted_terms`
  = `{policy_version, slot_minutes, reservation_duration_minutes,
  cancellation_cutoff_minutes, opening_hours, capacities}` of the selected policy (no
  `effective_from`). Policy 0 capacities = fixture table capacities.
- **S3-25** Seeded bookings: revision 1, policy-0 terms. Old idempotent replays return the
  original body (original revision and terms).
- **S3-26** Cancel checks the accepted cutoff against the current start; success increments
  revision once; a repeated cancel does not.
- **S3-27** A real amendment: old accepted cutoff first, then all resulting fields validated
  against the policy of the resulting start date; replaces accepted terms and end time
  atomically; revision +1 exactly once; one `changed` history entry.
- **S3-28** A no-op amendment keeps terms, end time, revision; no history; still requires a
  confirmed booking outside its cutoff.
- **S3-29** Failed amendments change nothing.
- **S3-30** `expected_revision` (optional, PATCH and per move): must be a positive integer
  (else 422); differing from current → 409 `stale_revision`, checked after 404 and before
  `reservation_cancelled`, cutoff and validation. Omitted → stage-1 semantics.
- **S3-31** Concurrent amendments with the same expected revision: at most one real change
  succeeds.
- **S3-32** History entries carry the resulting `revision` and complete `accepted_terms`;
  old entries never change.
- **S3-33** `GET /reservations/{reference}/decision` → exactly `{reference, revision,
  accepted_terms}`, also after cancellation; 404 for non-owner and without a token.

## E. Recurring reservations (series)

- **S3-34** `POST /series {anchor_reference, count, interval_weeks}` needs a token (401) and
  an idempotency key. `count` integer 2..12, `interval_weeks` integer 1..4, `anchor_reference`
  string; otherwise 422.
- **S3-35** Order: 401 → 400 body → key checks → replay/reuse → 422 fields → 404 anchor
  (unknown / not the caller's) → 409 `reservation_cancelled` → 409 `already_in_series`
  (anchor or any generated occurrence already in a series) → 409 `cutoff_passed` (anchor's
  accepted cutoff) → occurrence checks.
- **S3-36** Occurrence i (1..count−1): anchor's local date + i·interval_weeks·7 days, same
  local clock time, anchor's party size and table selection; selects its own date's policy;
  obeys opening hours, grid, capacity, DST (nonexistent → `invalid_local_time`; repeated →
  first occurrence) and occupancy. The first failing occurrence (index order) decides the
  ordinary booking error; nothing at all survives a failure (no reservations, history,
  counters, idempotency record).
- **S3-37** 201 `{series_id, revision: 1, interval_weeks, occurrences: [{index, reference,
  exception: false, reservation}]}`, all `count` occurrences in index order; index 0 is the
  anchor, unchanged (reference, identity, revision, terms, history, timestamps, original
  replay).
- **S3-38** Generated occurrences are ordinary reservations: listed, occupying tables,
  history `created`, revision 1, own terms.
- **S3-39** `GET /series/{id}` → same shape with current states; non-owner or no token → 404.
- **S3-40** A real PATCH of an occurrence marks it `exception: true` permanently and
  increments series revision once; no-op or failure changes neither.
- **S3-41** Cancelling an occurrence increments series revision once and keeps the
  occurrence (status cancelled, `exception` unchanged); repeated cancel does nothing;
  cancelling the anchor does not cancel siblings.
- **S3-42** Replays return the original series response (even after later changes) with 200
  and change no counter.
- **S3-43** Moves: each changed series occurrence becomes an exception; each affected series
  revision +1 once per batch; failed batch or replay changes nothing.

## F. Collective moves under policies

- **S3-44** Each real change in a move uses PATCH semantics (old cutoff, then new date's
  policy); optional per-move `expected_revision` (422 invalid, 409 `stale_revision`).
  No-op items keep terms and history. Every changed booking gains one revision and one
  `changed` entry. Failure leaves everything unchanged.

## G. Upgrade

- **S3-45** Imports exports of the stage-1 and stage-2 builds: sessions, references,
  replays and occupancy keep working; imported bookings read revision 1 (2 if cancelled)
  with policy-0 terms, have history, and can be adopted into a series.
- **S3-46** Export/import of a stage-3 service preserves policies, versions, histories,
  revisions, series, exception flags and all receipts.
