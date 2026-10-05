# Project rules — tablekeeper result repo
## What this is
Hackathon result repo: one folder per stage (stage-N/) with a Dockerfile, RUN.md and source of a
restaurant reservation HTTP service. Built by a band of seats (architect / drafter / fixer).
## Must-know / gotchas
- Each stage folder solves its own stage only; copy forward after acceptance.
- Never open the official test sources in the kickoff package; build to the specification.
- spec/stage-N.md = numbered requirements; tests/stage-N = acceptance suite (architect-owned).
## How to run / test / build
- `tests/run.sh N` builds stage-N, starts it, runs suites 1..N.
- Official: `cd /Users/arseniy/hack/band_my/ref && .venv/bin/python -m harness run --track tablekeeper --repo <repo> --stage N --mode isolated --out <new dir>`
## Conventions
- Commit source files by name; no caches or build output; never rewrite history.
## Safety rails
- No credentials in the repo.
