# Ladder log

| Stage | Task | Rung that finished it | Attempts (drafter / fixer / architect) | Selected tests |
|---|---|---|---|---|
| 1 | S1-T1 core API (S1-01..66) | fixer (drafter: 225/242, 239/242, no room report; fixer c6ad115: 241/242, last one needs S1-T2) | 2/1/0 | tests/run.sh 1 -k "not moves_export" |
| 1 | S1-T2 export/import + moves (S1-67..82) | fixer (drafter: 261/289 twice, WIP only, no room report; fixer 6a4f750: 289/289) | 2/1/0 | tests/run.sh 1 |
| 1 | S1-T3 fixture validation, PORT, cleanup (S1-02, S1-19a) — from final review + official check 117/120 | drafter (85db7d6: 314/314 first attempt) | 1/0/0 | tests/run.sh 1 |
| 2 | S2-T1 combined tables API + upgrade (S2-01..25) | drafter (afff38f: 344/344 first attempt) | 1/0/0 | tests/run.sh 2 -k "not s2_ui" |
| 2 | S2-T2 browser UI (S2-26..48) | fixer (drafter: ed7af1a 370/390, 3a046fe 357/391, no room report; fixer fc53a34 rewrite: 391/391) | 2/1/0 | tests/run.sh 2 |
