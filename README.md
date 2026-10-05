# The Dark Factory: tablekeeper

WeAreDevelopers × Band hackathon entry. Track: **tablekeeper** (a restaurant reservation
system: a table must never be double-booked, under concurrency, retries and time zones).

A three-seat Band Desktop factory built all four stages from **one dispatch message**:
an Opus architect writes the requirements and tests first, then every task climbs a ladder
from Haiku to Sonnet to the architect itself.

**Result:** every stage folder claims its stage on the official checks in isolated mode;
265 of 266 tests in our own spec-derived grader; 5 h 26 min; $27.46 list-price equivalent.

## How to read this repository

| Path | What it is |
|---|---|
| `FACTORY.md` | The factory: seats, design choices, costs, failure handling, what did not work |
| `mandates/` | One mandate per seat (`architect`, `drafter`, `fixer`), each naming its harness and model |
| `room.json` | The Band room of the submitted run, downloaded unchanged |
| `stage-1/` … `stage-4/` | One buildable service per stage (`Dockerfile`, `RUN.md`, source). All code here was written by the seats |
| `spec/` | The architect's requirement ledgers (`stage-N.md`) and the per-task ladder record (`ladder.md`) |
| `tests/` | The architect's black-box acceptance suites, written before the code |
| `evidence/` | Stage reports, builder reports, UI screenshots |
| `factory/` | Seat setup script, per-seat launchers, seat settings, the exact dispatch message |
| `EXPERIMENTS.md` | Every measured run of the factory, with time, cost and the change it forced |

## Run a stage

From a stage folder:

```bash
docker build -t tablekeeper . && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper
```

Then open http://localhost:8080 (stages 2 to 4 have a browser UI; stage 1 is API only).

## Team

Arseniy Kirykouski. Code under `stage-N/`, `spec/` and `tests/` was written by the seats;
the human wrote the dispatch, the mandates, `README.md`, `FACTORY.md`, `EXPERIMENTS.md` and
`factory/`.
