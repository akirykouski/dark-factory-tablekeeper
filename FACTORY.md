# FACTORY

A three-seat dark factory for Band Desktop that turns a written specification into a
staged, containerised service with one human message. It rests on two ideas:

1. **The shipped checks are a sample; the specification is the test.** The strongest model
   reads the spec line by line, turns every normative sentence into a numbered requirement,
   and writes a black-box acceptance suite for it **before any code exists**.
2. **Use the cheapest model that can pass that suite.** Every task climbs a ladder: Haiku
   tries first, Sonnet takes over only if Haiku cannot make the tests green, and Opus
   finishes a task only if both fail. Opus re-runs every claimed success itself, so a cheap
   model can be wrong as often as it likes. It cannot be wrong *and accepted*.

Nothing in `mandates/` names a product, an endpoint, a field or an error code. Point the
same three seats at a different specification and they work the same way.

## Seats

| Seat | Harness | Model | Owns | Never does |
|---|---|---|---|---|
| `architect` | Claude Code | claude-opus-5-5 | requirement ledger (`spec/stage-N.md`), acceptance suites (`tests/`), task split, ladder routing, every acceptance, stage promotion, reports | accepts a claim it has not re-run itself |
| `drafter` | Claude Code | claude-haiku-4-5-20251001 | first two attempts at every task | edits tests, accepts its own work |
| `fixer` | Claude Code | claude-sonnet-5-5 | two more attempts at any task the drafter could not finish, starting from its last commit and failing output | edits tests, accepts its own work |

The mandates are in `mandates/`; each starts with its `Harness:` and `Model:` lines.

### Why this split

- **Misreadings cost more than keystrokes.** In a spec-driven build the expensive mistakes
  are missed clauses: error precedence, wrong JSON types, replays, time-zone edges, upgrades
  of old data. So the strongest model spends its tokens on reading and testing, not typing.
- **The ladder is the cost lever.** On the practice track the ladder matched the five-seat
  factory's hidden score (34/34) for $1.90 against ~$13. On tablekeeper the full run cost
  $27.46 against $49 to $65 for the five-seat factory.
- **The gate makes the cheap rungs safe.** The architect re-runs the selected tests on the
  builder's commit before accepting. A success it cannot reproduce counts as a failed rung,
  and the task moves up.
- **Tests are owned by one seat.** Builders never edit tests. A test the architect later
  finds wrong it fixes itself, quoting the spec (this happened twice in the final run, both
  times flagged by the fixer).

## The pipeline (per stage)

```
human dispatch (once, all four stages)
  └─▶ architect: spec/stage-N.md  every normative sentence → S<N>-<k> + acceptance criterion
      architect: tests/stage-N/   black-box suite, run once against nothing to prove it fails
      architect: split into tasks, each with requirement ids + exact test selection
        for each task:
          @drafter  attempt 1, attempt 2 ──green?──▶ architect re-runs ──▶ accept
                │ not green
          @fixer    attempt 1, attempt 2 ──green?──▶ architect re-runs ──▶ accept
                │ not green
          architect finishes it
        record rung, attempts and test counts in spec/ladder.md
      architect final check: every suite up to N + official checks (isolated)
                             + diff review for test-only code + run-instructions verbatim
                             + headless browser at 375 px and 1280 px for UI
      accept → copy stage-N to stage-(N+1) unchanged → next stage
```

## Setting it up

Requirements: Band Desktop with the `band` CLI, Claude Code, a Claude subscription, Docker.

1. **Clean seat home.** Seats run Claude Code with their own config directory
   (`factory/claude-home/`), so no personal CLAUDE.md, hooks, plugins or MCP servers leak
   into unattended seats. Log in once:
   `CLAUDE_CONFIG_DIR=factory/claude-home claude` → `/login` → `/exit`.
   `factory/claude-home/settings.json` turns auto-memory off and holds `permissions.deny`
   rules that block every seat from reading the official test sources. We did not trust the
   prompt alone to keep seats out; the deny rules hold under bypass mode (verified). Edit the
   absolute paths to your kickoff checkout.
2. **Seats.** `factory/setup-seats.sh <absolute result repo path>` creates the three seats as
   Band-owned headless Claude Code runtimes, live-linked to `mandates/*.md`, each with
   `factory/bin/seat-<name>` as its spawn command. The launcher exports the seat's git
   identity (`<seat>@factory.local`) to its whole process tree, so every commit and merge is
   authored by the seat that made it, and disables auto-memory.
3. **Room.** Create a room in Band Desktop, add the three seats, send the dispatch
   (`factory/dispatch.md` is the exact message used for the run in `room.json`).
4. **Watch, do not steer.** The operator only reads. The architect keeps one background
   wake-up timer pending while a stage is open and chases silent seats itself.

## How it catches and recovers from bad work

| Mechanism | What it catches |
|---|---|
| Requirement ledger with acceptance criteria | Clauses no example shows: precedence, boundaries, atomicity, replays, time edges, upgrades |
| Tests first, from the spec, by the seat that does not build | Code that passes the shipped sample but not the spec |
| Test checklist in the architect mandate: per-field type matrix, whitespace and newline variants, exact response field sets, concurrency bursts of 20 to 50, upgrades in every lifecycle state, UI at 375/1280 px with lost and late responses | The defects our earlier runs actually shipped (see EXPERIMENTS.md) |
| Re-run before accept | Unreproducible claims, unreported failures, missing files |
| Ladder escalation after two attempts | A builder stuck on the same defect |
| Diff review at stage close | Code that only makes sense for a test input |
| Overshoot rule and folder-docs rule | A stage folder that already solves the next stage; copied run instructions that name another stage |
| Wake-up timer plus "never end a turn without a message to the next actor" | A seat that finished work but never said so; an empty message that wakes nobody |
| Probe-before-reject | Rejections based on the architect's own wrong expectation |

## Measured results

Cost is the list-price equivalent of the seats' token usage (ccusage over the seats'
dedicated Claude home); the seats ran on a Claude subscription.

**The submitted run** (`room.json`, this repository):

| | |
|---|---|
| Human input | 1 message (the dispatch) |
| Wall clock | 5 h 26 min, 12:53 to 18:19 UTC, Oct 5 |
| Official checks, `--all --mode isolated` | every folder claims its stage (120 / 25 / 7 / 6 shipped checks) |
| Our shadow grader (266 spec-derived tests, never shown to seats) | 265 / 266 (login with a missing field answers 400, the spec says 422) |
| Factory's own suite at stage 4 | 516 tests |
| Tasks | 9: drafter finished 4 (all first attempt), fixer 5, architect 0 |
| Cost | $27.46: Opus $16.02, Haiku $6.72, Sonnet $4.72 |

Per-task record: `spec/ladder.md`. Stage reports and the final report: `evidence/`.

**How we got here.** Sixteen measured runs, all logged with time, cost and the rule each one
forced in `EXPERIMENTS.md`. Highlights:

| Run | Factory | Result | Wall clock | Cost |
|---|---|---|---|---|
| E2 to E10 | five seats (Opus lead, tester, reviewer; Sonnet backend, frontend) | 4/4 claimed in every completed run; shadow grader 264 to 266 / 266 | 1.5 to 3.2 h | $49 to $65 |
| L1 | ladder, practice track | 34/34 hidden | 59 min | $1.90 |
| L-P | ladder, pocketful stage 1 | 147/147 hidden | ~3 h | ~$9 |
| **FINAL** | **ladder, tablekeeper** | **4/4, 265/266** | **5 h 26 min** | **$27.46** |

The ladder is slower than the five-seat factory (one architect serialises all review) and
about half the price at the same quality.

## What we tried that did not work

- **Five seats with two Opus judges.** Good quality, but about twice the cost; the tester
  and reviewer duplicated much of the architect's reading. Merged into one architect.
- **Opus builders (E8).** Same stage times, 1.5x the cost, no quality gain.
- **Reminders instead of structure.** "Update the copied folder's run instructions" was
  followed once and then forgotten over a two-hour run. Replaced by a rule that makes the
  mistake impossible: run instructions never name a stage or a folder.
- **Machine git identity.** Telling seats `git -c user.name=…` was not enough: a `git merge`
  still used the machine identity. Fixed in the per-seat launcher.
- **Seats only wake on messages.** A builder described a finished fix in its own thoughts and
  ended its turn; the band sat idle. Fixed with message-per-turn and the architect's timer.
  Haiku also twice ended a turn with an empty message; the drafter mandate now says an empty
  message reaches nobody.
- **Hidden seat memory.** Claude Code auto-memory had accumulated 13 files across early runs.
  Memory is now off; the generic lessons were written into the mandates instead.
- **Reusing seats across runs.** A stale session from a stopped run committed into the next
  run's repository. The submitted run used freshly created seats, a fresh room and a fresh
  repository.
- **No defence against losing the credential.** One run died when subscription access was
  briefly disabled for the account. The operator has to watch runtime errors, not only
  progress.
