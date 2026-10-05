# Ladder log

| Stage | Task | Rung that finished it | Attempts (drafter / fixer / architect) | Selected tests |
|---|---|---|---|---|
| 1 | S1-T1 core API (S1-01..66) | fixer (drafter: 225/242, 239/242; fixer c6ad115: 241/242, last one needs S1-T2) | 2/1/0 | tests/run.sh 1 -k "not moves_export" |
| 1 | S1-T2 export/import + moves (S1-67..82) | fixer (drafter: 261/289 twice, WIP only; fixer 6a4f750: 289/289) | 2/1/0 | tests/run.sh 1 |
| 1 | S1-T3 fixture validation, PORT, cleanup (S1-02, S1-19a) — from final review + official check 117/120 | drafter (85db7d6: 314/314 first attempt) | 1/0/0 | tests/run.sh 1 |
| 2 | S2-T1 combined tables API + upgrade (S2-01..25) | drafter (afff38f: 344/344 first attempt) | 1/0/0 | tests/run.sh 2 -k "not s2_ui" |
| 2 | S2-T2 browser UI (S2-26..48) | fixer (drafter: ed7af1a 370/390, 3a046fe 357/391; fixer fc53a34 rewrite: 391/391) | 2/1/0 | tests/run.sh 2 |
| 3 | S3-T1 explain, history, policies, terms, revisions (S3-01..33, 44) | fixer (drafter c854d6f: declined, no code; fixer d5c50df: 472/472 after 2 test fixes by architect) | 1/1/0 | tests/run.sh 3 -k "not s2_ui and not s3_series_upgrade" |
| 3 | S3-T2 series + upgrades (S3-34..46) | drafter (9293d40: 457/457 first attempt) | 1/0/0 | tests/run.sh 3 -k "not s2_ui" |
| 4 | S4-T1 restaurant revision + replans (S4-01..17) | fixer (drafter: 26d8523 453/485 step 1 only; 2b8f1d9 475/485; fixer e108f23: 485/485) | 2/1/0 | TK_NO_PREV=1 tests/run.sh 4 -k "not s2_ui and not test_amend" |
| 4 | S4-T2 series amend + upgrades (S4-18..25) | drafter (7e0248b: 516/516 first attempt) | 1/0/0 | tests/run.sh 4 -k "not s2_ui" |

Note: builder room reports were queued and reached the architect only after stage 4 was accepted, so every rung above was verified by re-running the selected tests on the builder's commit, not by reading its report.
