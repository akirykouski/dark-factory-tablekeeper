# Factory experiments log

Each entry: config, dispatch, outcome (checks, time, cost), what broke, what changed next.

## E0 — plumbing (2026-10-03 02:00–02:20 CEST)
- Band Desktop 0.4.12, seats via `band agent create` (claude-code-cli transport, owned runtime).
- `bare` context mode cannot use a Claude subscription → seats use `local_config` with a
  dedicated CLAUDE_CONFIG_DIR (wrapper `bin/claude-seat`) so no personal config leaks in.
- `band chat add` prints a decode error ("expected unit") but the add succeeds; verify with
  `band chat participants`.
- Agent-created rooms do not include the human; add the human identity explicitly.

## E1 — toy rehearsal, 5 seats (lead/tester/reviewer Opus 5.5, backend/frontend Sonnet 5.5)
- Dispatched 02:23 CEST, all 4 stages in one message (dispatch/toy.md).
- Result: all 4 stages accepted, 00:23→00:57 UTC (**34 min**). Independent `harness run --all
  --mode isolated`: every folder claims its stage at 100%.
- Cost (ccusage on claude-home, list prices): Opus $10.82, Sonnet $2.12 → **~$13**.
- Commits on main by author: backend 8, tester 7, reviewer 6, frontend 5, lead 4.
- Review changed work 3 times: (1) reviewer rejected UI showing stale value after reset;
  (2) lead caught a tester stage-1 test asserting a feature's absence that stage 2 adds;
  (3) tester found chunked request bodies ignored in stage 4 → reviewer rejected already
  merged commit → backend fixed in <1 min.
- Tester spontaneously ran a mutation check (remove the lock → 24/25 concurrency tests fail).
- Frictions: lead omitted the official check command in builder handoffs; promotion commits
  landed on main from backend although the reviewer mandate says reviewer-only main.
- Changes for E2: lead handoff checklist (verbatim check command, owners of other items),
  explicit promotion exception, stage-gating of "absent until later" tests.

## E2 — tablekeeper candidate run, same 5 seats, mandates after E1 fixes
- E2 dispatched 00:59 UTC, room 455ead28-c197-4635-b60e-d47a96e17c5c
- Stage 1 accepted 01:21 UTC (**~21 min**). Ledger 177 entries, 7-part handoffs. Review
  rejected backend once (import validation) and tester once (repo hygiene).
  My independent isolated check: stage 1 **120/120** shipped, suite 2 fails as expected.
- Started a shadow grader (factory/shadow/, spec-derived, never shown to seats) to estimate
  hidden-test performance across experiments.
- Stage 2 accepted 01:44 UTC (~23 min). Reviewer rejected the first combined revision once.
  My isolated check: suite 1 120/120, suite 2 25/25, suite 3 fails (no overshoot).
  UI review (my screenshots 1280/375): coherent warm hospitality design, human labels,
  legend, combined rows, big confirmation reference; no page h-scroll. Nits: mobile header
  wraps "Log out"; grid scrolls inside its card on 375 px without a visual cue.
- Shadow grader (266 spec-derived tests, factory/shadow) on E2 stage-2 snapshot 77248fc:
  stage 1 **145/145**, stage 2 **32/32** (after fixing 2 shadow-test bugs found in triage).
- Stage 3 accepted 02:04 UTC (~20 min). Isolated shipped checks: 120/120, 25/25, 7/7; suite 4
  4/5 (not whole → no overshoot). Shadow grader: **145/145, 32/32, 53/53** (one more shadow
  test bug fixed: stage 2 legitimately adds a slot field).
- Stage 4 accepted 02:25 UTC. **Whole run 01:00→02:25 UTC = 85 min, 0 human input.**
  `harness run --all --mode isolated`: every folder claims its stage (s1 120/120; s2 +25/25;
  s3 +7/7; s4 +6/6). Shadow grader on stage-4: **266/266** (after fixing 2 more shadow setup bugs).
- Cost E2 ≈ **$49** list price (all seats; total today $61.75 minus E1 $12.96).
- Commits (all branches): tester 15, backend 14, frontend 6, lead 5, reviewer 5 (+ all 10
  reviewer merges to main). Room text messages: lead 63 (multi-part handoffs), backend 15,
  reviewer 8, tester 8, frontend 6.
- Review rejections: 3, all in stages 1–2 (backend import validation, tester hygiene, combined
  UI revision). Stages 3–4 accepted first time → suspicious; shadow grader too lenient to tell.
- Observation: lead routes stage 3/4 server modules to frontend ("no new screens"), so the
  frontend seat carried real server work — good distribution.
- Next: adversarial red-team probes of the E2 build to find what the factory missed.
- **Miss found by me:** every stage folder's RUN.md still says "stage 1" and its command
  builds `stage-1` (copied forward, never updated). Harness builds the Dockerfile directly so
  checks pass, but a judge following stage-N/RUN.md by hand gets the stage-1 service. The
  reviewer's "follow run instructions literally" did not catch it (it built the folder path,
  not the RUN.md command). → E3 fix: promotion must update stage-specific references; reviewer
  must execute the RUN.md command verbatim from the stated directory and confirm it serves
  this folder's stage.
- Red team 1 (stages 1–2 + UI, ~650 probes, Playwright incl. lost responses, out-of-order
  searches, cross-stage upgrade in-page): **0 confirmed violations**; 8 low ambiguities
  (e.g. unparseable body without token → 400 before 401).
- Red team 2 (stages 3–4, ~4,200 assertions + 1,400 garbage requests, planner vs brute force
  on 800 random cases, imports of stage 1/2/3 exports into stage 4): **0 confirmed
  violations**; 11 low ambiguities.
- Conclusion: E2 product quality is high; the only real defect is stale per-stage docs.

## E3 — submission candidate: E2 factory + documentation-carry-forward fix
Mandate changes vs E2:
1. backend (promotion): after the unchanged copy commit, update every stage-specific
   reference in the copy (titles, image names, folder paths in commands) in the first
   commit of the new stage.
2. reviewer: run each stage folder's RUN.md command verbatim from the directory it names and
   prove the running service is that folder's stage; reject stale stage references.
3. frontend: navigation fits a 375 px viewport without awkward wrapping; scroll containers
   show that they scroll.
- E3 dispatched 02:56 UTC, room 2fd82afe-f64c-43b6-bbf0-f942d25c1667, fresh repo
- **E3 aborted at ~4 min:** lead committed the ledger under the machine's default git
  identity ("Arseniy") — E2's lead had set `user.name=lead` on its own, the mandate never
  required it. A human-looking commit in the submitted repo reads as hand-built work, and
  editing a mandate mid-run would be steering, so the run was stopped (seats stopped,
  repo archived to archive/e3-result). Fix: every mandate states the commit identity and
  forbids the default identity / config changes. Restarted as E4.

## E4 — submission candidate: E3 changes + seat commit identity + worktrees outside repo
- Seats deleted and recreated (same names, fresh state) after E3 abort.
- E4 dispatched 03:08 UTC, room 50ca7e92-f431-4c58-94d3-13e048734af1, fresh repo.
- E4 stage 1 accepted 03:35 (~27 min) after 6 reviewer rejections (frontend 3, backend 2,
  tester 1 hygiene) and a lead ruling on a disputed edge case. Stage 2 accepted 04:12 (~34 min).
- Commit identities correct (lead/backend/frontend/tester/reviewer @factory.local); worktrees
  beside the repo. RUN.md per folder names and builds its own stage (fix verified).
- Implementation diverged from E2 (Starlette vs stdlib) → runs are not copies of each other.
- UI (my screenshots): mobile nav fits one line, explicit "swipe sideways" cue on the grid,
  desktop two-column layout with sticky reservation card. Better than E2.
- E4 stage 3 accepted 04:57, stage 4 + FINAL 05:35 UTC → **2h27m**, 0 human input.
  `--all --mode isolated`: every folder claims its stage. Shadow grader **266/266**.
- Room: 185 text messages (lead 108, reviewer 29, backend 17, frontend 15, tester 11);
  **8 rejections** (vs 3 in E2). Commits: backend 16, tester 16, lead 7, frontend 7, reviewer
  4 + 8 merges. Cost ≈ **$65** list price.
- **Still a docs miss:** stage-3/4 RUN.md say "stage 2" and "Run this from the `stage-2/`
  folder". The "update references after copy" rule was followed at stage 2 only; reviewer's
  verbatim run (`docker build .` from inside the folder) did not notice the wrong folder
  name. Lesson: reminders decay over a long run; make the error impossible instead.

## E5 — submission candidate: structural docs rule
- Run instructions never name a stage number or folder ("from this folder") → copying a
  folder cannot leave them wrong. In lead (every packaging handoff), backend, frontend;
  reviewer greps each folder for mentions of other stages and rejects every hit.
- E5 dispatched 05:40 UTC, room 1aa4d224-142f-4674-b3e0-8f66595e6913, fresh repo, same seats as E4
- Red-team suites re-run on E4: **0 confirmed violations** (planner oracle 8 seeds 0
  mismatches; policy fuzz 1316/1316; upgrades 1→4, 2→4, 3→4). 8 behaviour differences vs E2,
  all in ambiguous corners (e.g. E4: bad JSON without token → 401, E2 → 400; E4 accepts
  `expected_revision: null` as omitted). Two independent runs, two different stacks, same
  conformance → the factory, not luck, carries the quality.
- E5 FINAL 07:16 UTC → **1h36m**, 0 human input. `--all --mode isolated`: every folder claims
  its stage (120 / 25 / 7 / 6). RUN.md in every folder says "from this folder", no stage
  names anywhere in the folders → structural rule works.
- Room: 160 messages (lead 100, reviewer 20, backend 15, frontend 14, tester 6); 7 review
  defects. Commits: lead 19, backend 17, frontend 13, reviewer 12 (+12 merges), tester 7.
- **Shadow grader 264/266 — first real discrimination between runs.** Two spec deviations
  the room missed: (1) starts_at_local given as a JSON number → 422 (spec: only a wrongly
  formatted *string* is 422; a wrong JSON type is 400; E2/E4 returned 400); (2) restaurant
  detail gained a changing `revision` field although "the ordinary restaurant detail still
  returns its original fixture configuration".
- Both are rigor gaps, not luck: no seat enumerated every JSON type per input field, and no
  seat checked that responses carry only specified fields. → E6 mandate changes.

## E6 — submission candidate: + type matrix, exact response fields
- E6 dispatched 07:40 UTC, room 5923a4d8-97f0-46ff-94c6-b667172a7186, fresh repo.
- E6 FINAL 09:42 UTC → **2h02m**, 0 human input. `--all --mode isolated`: 4/4 claimed.
  **Shadow grader 266/266** (both E5 misses gone: wrong JSON type → 400, detail has no extra
  field). RUN.md clean in every folder. Cost ≈ **$55**. Room: 114 messages (lead 63, reviewer
  13, backend 13, tester 12, frontend 8); 5 rejections incl. a stage-4 planner-occupancy
  defect caught by the reviewer.
- **Identity miss again:** one `git merge main` on a builder branch was authored with the
  machine identity (the mandate's `-c user.name` only covers `commit`). History cannot be
  rewritten, so E6 carries it. Structural fix for E7: per-seat launchers (`bin/seat-<name>`)
  export GIT_AUTHOR_*/GIT_COMMITTER_* for the seat's whole process tree; verified a commit
  from a child shell is authored and committed as the seat.

## E7 — submission candidate: E6 + per-seat git identity in the runtime
- E7 dispatched 09:47 UTC, room 70fa520f-1f1f-4844-994b-474d0dc57258, fresh repo.
- E7 FINAL 11:22 UTC → **1h35m**, 0 human input. `--all --mode isolated`: 4/4 claimed
  (120/25/7/6). **Shadow grader 266/266.** Every commit and merge authored and committed by a
  seat identity (launcher fix verified). RUN.md clean. Cost ≈ **$49**. Room: 86 messages
  (lead 40, backend 16, reviewer 9, frontend 8, tester 8); 2 rejections plus a reviewer-caught
  defect in the tester's own suite (fixed by tester).
- **E7 chosen as the submission run** (cleanest: no docs miss, no identity miss, no spec
  deviation found by shadow grader). Mandates frozen in factory/submitted-e7/.

## E8 — model experiment: E7 factory with backend + frontend on Opus 5.5 (all five seats Opus)
- Purpose: measure whether stronger builders change quality, time or cost. Not a submission candidate (mandates differ only in the Model line).
- E8 dispatched 11:37 UTC, room 254873fd-b5ed-407a-bb48-343730743591.
- Red team on E7: type matrix 1,038 cases 0 failures; 0 extra/missing fields in ~550 response
  structures; restaurant detail identical to the fixture after every kind of write; UI
  probes all pass; 1,428-request fuzz 0 5xx. **1 real violation (medium):** a trailing
  newline passes format checks (`…T19:00\n`, `date=…%0A`, `party_size=2%0A`, `HH:MM\n`) and is
  stored/echoed — the regex-`$` trap. Generic lesson for E9: format validation must match the
  whole input; the tester's matrix must include trailing/leading whitespace and newlines.
  (Also: E7 scopes an idempotency key per user across paths — the literal §7 reading.)
- Red team on E5 (final): 0 hard violations beyond the two known; ~1,200 type probes 0 5xx;
  regression R1: a booking cancelled on stage 1/2 imports into stage 4 with history lacking
  the `cancelled` entry. Lesson for E9: upgrade tests must cover records in every lifecycle
  state and require imported records to read like natively created ones.
- **E8 stalled at 13:24 UTC (liveness failure).** Backend finished a fix and described it in
  a *thought*, never in a message; the reviewer waited for it; the lead's turn had ended;
  seats only wake on messages → whole band idle for 26 min with no way out. This failure
  mode would end a submitted run silently.
- Probe (human message, E8 now non-submittable): asked the lead to start a background
  `sleep 90` and report when it fired → the lead was woken by the background completion 90 s
  later with no message, and posted. **Self-scheduled wake-ups work in Band owned runtimes.**
- Fixes (E9): (1) every seat: a turn that produces a result/decision/blocker ends with a
  message to the next actor; thoughts are invisible; (2) lead: liveness heartbeat — keep
  exactly one background timer pending while a stage is open; on wake diff repo + status
  table, re-send full tasks to silent seats, reassign after two silent requests. Also applied:
  full-match format validation, whitespace/newline cases in the type matrix, upgrade tests
  over every lifecycle state.
- 13:53 UTC: told E8's lead to re-read its mandate's Liveness section and resume (test of the
  heartbeat on a live stall).
- The heartbeat worked on the live stall at once: the lead found backend's unposted commit
  (5d5b937) by diffing branch heads, routed it to the reviewer, armed a 600 s timer.
- **Hidden state found:** seats share Claude Code auto-memory per working directory; 13
  memory files had accumulated across runs (e.g. probe-before-reject, private ports,
  calendar extremes, reviewer merge identity). Runs E5–E8 therefore had knowledge the
  mandates did not contain → not reproducible from `mandates/` alone. Fix: auto-memory off
  for seats (settings `autoMemoryEnabled: false` + `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in the
  launcher), memory wiped before E9, archived in factory/archive-memory/after-e8. The
  generic lessons were promoted into the mandates as explicit text (reviewer: probe-before-
  reject, failures by name, own browser pass, calendar extremes, merge-by-revision on
  crossed messages, never reset main; tester: runner forms, calendar extremes; lead:
  interface-first split; all: zsh quoting, private container ports, commit hygiene).

## E9 — submission candidate: + liveness heartbeat, message-per-turn, full-match validation, upgrade states, promoted lessons, no seat memory
- E8 stopped at stage 3 (cost ≈ $47 for ~2.5 stages with Opus builders vs E7 ≈ $49 for 4 stages with Sonnet builders, same stage times) → keep Sonnet builders.
- Seats recreated from setup-seats.sh (verifies the documented setup), memory wiped and disabled.
- E9 dispatched 14:08 UTC, room 7599312a-f2ea-4a78-b018-ff4c80343dd5.
- E9 FINAL REPORT 17:21 UTC → **3h13m** (stricter gate costs time), 0 human input. `--all
  --mode isolated`: 4/4 claimed (120/25/7/6). **Shadow grader 266/266.** Trailing newline /
  space now 422 (E7 bug fixed). Every commit seat-authored; 0 symlinks; 0 nested repos;
  RUN.md clean. **0 seat memory files after the run** (memory off verified). Heartbeat fired
  2 real status requests. Room: 147 messages (lead 85, backend 19, reviewer 19, frontend 11,
  tester 8); 4 rejections. Cost ≈ **$62**.
- Oddity: after FINAL REPORT the lead posted a stale "Stage 1 started" status (a delayed
  send); harmless.
- **E9 replaces E7 as the submission run**: built from the final mandates with no hidden
  memory (E7's seats had 13 accumulated memory files), plus liveness and full-match rules.
  Mandates frozen in factory/submitted-e9/.
- E9 UI (my screenshots): most polished so far (chosen-cell check mark, combined-tables
  band, confirmation card, mobile nav fits, swipe cue). Defect: after a *successful*
  booking the grid is not refreshed, so slots the new booking now occupies still read
  "Free" (spec only mandates refresh after a 409, so compliant; clicking one gives
  booking-error + refresh). E7 refreshed. Candidate generic rule: after any successful write,
  refresh every view the write changed.

## E10 — submission candidate: E9 + refresh views after successful writes
- E10 dispatched 18:12 UTC, room cba20ccd-c055-4b39-a4c1-c4893b1141ec, fresh repo, memory wiped.
- Red team on E9: E7 newline bug fixed everywhere (~1,000 whitespace/unicode variants incl.
  CRLF, NBSP, U+2028, unicode digits: 0 accepted, 0 5xx); type matrix 0 strict failures;
  0 extra fields; restaurant detail stable; planner oracle, imports 1→4/2→4/3→4 incl.
  lifecycle states, policy fuzz 1316/1316 all pass. **1 real violation (med-low, regression
  vs E7):** a no-op series amend inside the cancellation cutoff returns 409; the spec checks
  the cutoff only for real changes. Lesson (staged for E11): classify no-ops before applying
  per-change guards; tester/reviewer test the no-op variant of every write where a real
  change would be refused.

## T2 — toy benchmark run with E10 mandates (df-bench Benchmark 2)
- E10 FINAL 21:33 UTC (3h21m): 4/4 claimed, all commits seat-authored, docs clean. Another stale first message delivered after the final report (Band delivery quirk).
- T2 dispatched 21:36 UTC, room 2e6bd8b5-df65-4ab4-8d8a-3220c0c3df7d.
- E10 verification: `--all --mode isolated` 4/4 claimed; **shadow grader 266/266**; docs clean;
  all commits seat-authored. **UI refresh fix works**: after a booking the grid shows the newly
  occupied slots as Full (desktop and 375 px); best UI so far (two-column desktop, combined-
  tables band, scroll hint). Still has the no-op-amend-inside-cutoff 409 (probe p13 on E10
  stage 4) → E11 rule needed. E10 is the fallback submission (frozen in submitted-e10/).
- **Operator error (T2):** archived band-work/result without recreating it; every seat's
  working directory is that path, so the lead's toy session could not start and the dispatch
  sat in its inbox for 30 min (22:07 UTC found, fixed by recreating the folder and restarting
  the session). Rule for operating the factory: the seats' working directory must always
  exist; after dispatch, confirm the lead's first tool call within 2 minutes.

## T2 toy (E10 mandates) result: 24/24 shipped, 34/34 hidden, 1h50 active (+30 min operator stall), ~$13.
## P1 — pocketful stages 1–2, df-bench Benchmark 1 protocol (no shipped tests, no check command, test dirs denied by permission rules), E10 mandates
- P1 dispatched 23:57 UTC, room faad8918-696f-4e4e-a3f0-6e536b4b16f3.

## L1 — ladder factory on toy (user's design): 3 seats
- architect (Opus 5.5: spec → requirements → edge-case tests first → split → per-task ladder →
  final check), drafter (Haiku 4.5: first builder, 2 attempts), fixer (Sonnet 5.5: second
  builder, 2 attempts); architect finishes a task itself if both fall short. Mandates in
  factory-ladder/mandates.
- Result: all 4 stages in **59 min** (incl. 19 min stall), df-bench **24/24 shipped, 34/34
  hidden** — same score as the 5-seat factory (E1, T2). **Cost $1.90** (Opus $1.60, Haiku $0.30,
  Sonnet $0, never called) vs ~$13 for the 5-seat factory on the same track.
- Haiku finished every task on its first attempt. Weakness: Haiku twice ended its turn with
  an empty message (mention only), which does not wake the architect; the architect's wake-up
  timer recovered after 19 min.

## L-P — ladder on pocketful stages 1–2 (df-bench Benchmark 1 rules), Oct 4
- Stage 1 accepted 13:45 UTC (~3 h, 4 tasks): Haiku finished all 4 tasks (3 first try, 1
  on the second); Opus's final review fixed 4 issues itself (signup race, UTF-8 chunk
  decoding, fixture shape guards, crash guards). Hidden score **147/147**. Cost ≈ $9
  (Opus $7, Haiku $4 for the day, minus the $1.90 toy run).
- **Run killed at 14:12 UTC** in stage 2: every seat failed with "Your organization has
  disabled Claude subscription access for Claude Code" (an account-level block, cleared by
  Oct 5 10:16). Lesson: the factory has no defence against its credential disappearing;
  the operator must watch for runtime errors, not only for progress.

## L2 — full tablekeeper ladder run (candidate), Oct 5
- Ladder mandates hardened with every lesson from the 5-seat factory, in generic form:
  architect's test checklist (type matrix, whitespace, exact response fields, precedence,
  atomicity, retries, no-ops, concurrency, time extremes, upgrades in every lifecycle
  state, UI states incl. refresh after write), product bar for UI checked in a real
  browser, overshoot and folder-docs rules, probe-before-reject; builders get the
  implementation rules. Vocab scan 0. Frozen in factory-ladder/submitted-l2/.
- Dispatched 10:18 UTC with the same tablekeeper dispatch as E10 (handle @architect).

## FINAL — clean ladder run (submission)
- L2 stopped at 12:40 UTC (stale Pocketful commit from the earlier stalled run had leaked into its history). For a clean run: old 5 seats stopped, ladder seats deleted and recreated from setup-seats.sh (empty inboxes verified), fresh room, fresh repo band-work/final, mandates identical to submitted-l2.
- Dispatched 12:53 UTC, room e1dc1dac-abb6-48e0-a369-02789151860e. Operator only reads from here on.
- Result: **all 4 stages accepted at 18:19 UTC (5 h 26 min)**, one human message. Official checks
  `--all --mode isolated`: stage 1 120, stage 2 25, stage 3 7, stage 4 6 shipped checks, every
  folder claims its stage. Shadow grader **265/266** (the one miss: login with a missing field
  answers 400, spec says 422). Every commit under stage-N/ is seat-authored; 0 symlinks.
- Ladder: 9 tasks. Drafter (Haiku) finished 4, each on its first attempt; fixer (Sonnet)
  finished 5; architect (Opus) finished 0 and only wrote specs, tests and verdicts. The
  architect fixed two of its own tests after the fixer pointed out they misread the spec.
- Cost (list-price equivalent, ccusage): **$27.46** = Opus $16.02 + Haiku $6.72 + Sonnet $4.72,
  against ~$49–65 for the 5-seat factory on the same track.
- Builder reports in the room were queued and reached the architect only after stage 4 was
  accepted; every rung was accepted on a re-run of the builder's commit, never on its report.

## External data from the parallel session (band-c6), Oct 5
- Solo Opus 5.5 (one `claude -p`, no Band), pocketful s1–3 under df-bench Benchmark 1 rules:
  147/147, 35/35, 6/6 in 25 min for $7.18. Solo Sonnet/Opus on toy: 24/24 + 34/34 hidden in
  ~2 min for $0.18/$0.50.
- But both solos shipped a real spec violation no suite catches: chunked request bodies
  ignored. Our own E1 factory's blind tester caught the same defect class in the toy (stage 4)
  and the reviewer rejected it — the band's value is catching what one agent can't see in its
  own work, not speed.
- Operator note: Band passes its own --settings to claude-code-cli seats; per-seat rules
  belong in user settings or the seat cwd's .claude/settings.json (ours are in the clean
  home's user settings.json, deny verified).
