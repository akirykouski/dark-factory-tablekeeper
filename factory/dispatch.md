@architect Build all four stages of the tablekeeper track, one after another, with the band. This is the only message you will get from me. Run each stage to acceptance, then continue to the next, without asking me anything.

Kickoff package (specs + official checks): /Users/arseniy/hack/band_my/ref
Track: tablekeeper
Specs (read each in full; paste the full text into every handoff):
  /Users/arseniy/hack/band_my/ref/tablekeeper/spec/stage-1.md
  /Users/arseniy/hack/band_my/ref/tablekeeper/spec/stage-2.md
  /Users/arseniy/hack/band_my/ref/tablekeeper/spec/stage-3.md
  /Users/arseniy/hack/band_my/ref/tablekeeper/spec/stage-4.md
Result repository (absolute path, commit only here): /Users/arseniy/hack/band_my/band-work/final
Stage N goes in /Users/arseniy/hack/band_my/band-work/final/stage-N/ (source, Dockerfile, RUN.md). When stage N is accepted, copy the folder to stage-(N+1) and extend the copy. Any language or framework is fine. Each folder must solve its own stage and must not already solve the next one.

Official checks (run from the kickoff package directory; every --out directory must be new):
  cd /Users/arseniy/hack/band_my/ref && .venv/bin/python -m harness run --track tablekeeper --repo /Users/arseniy/hack/band_my/band-work/final --stage N --mode isolated --out /Users/arseniy/hack/band_my/band-work/checks/sN-<unique>
The last line must read "claimed stage: N". The run also tries the next stage's suite; that line is expected to fail. The official checks are only a small sample of the grading suite (stage 1 about 80%, stage 2 about 40%, stage 3 about 10%, stage 4 about 20%); grading uses the full suite, and every graded behaviour is written in the specification. Do not open the official test sources under /Users/arseniy/hack/band_my/ref/tablekeeper/test; build to the specification.

Python for your own test tooling: /Users/arseniy/hack/band_my/ref/.venv/bin/python (pytest, httpx, playwright with chromium installed). Docker is available.

After each stage is accepted, post the stage report. When stage 4 is accepted, post the final report.
