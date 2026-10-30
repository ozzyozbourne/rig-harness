| arm | family | clean | correct | broke read-only | mean tokens | ctx edits | tool calls |
|---|---|---|---|---|---|---|---|
| summary | needles | 4/6 | 5/6 | 1/6 | 249,157 | 0.0 | 10.0 |
| clm | needles | 5/6 | 6/6 | 1/6 | 223,679 | 7.2 | 18.2 |
| clm_lessons | needles | 5/6 | 6/6 | 1/6 | 314,431 | 14.8 | 27.5 |
| clm_code | needles | 6/6 | 6/6 | 0/6 | 248,396 | 10.7 | 22.5 |
| summary | kv | 5/6 | 6/6 | 1/6 | 322,615 | 0.0 | 8.2 |
| clm | kv | 2/6 | 4/6 | 2/6 | 289,866 | 12.2 | 22.0 |
| clm_lessons | kv | 4/6 | 6/6 | 2/6 | 246,172 | 12.7 | 23.2 |
| clm_code | kv | 5/6 | 6/6 | 1/6 | 320,919 | 13.7 | 24.7 |
| summary | rules | 4/6 | 4/6 | 0/6 | 199,858 | 0.0 | 8.0 |
| clm | rules | 6/6 | 6/6 | 0/6 | 214,943 | 3.8 | 14.7 |
| clm_lessons | rules | 4/6 | 5/6 | 1/6 | 147,079 | 8.5 | 18.5 |
| clm_code | rules | 4/6 | 4/6 | 0/6 | 198,181 | 7.7 | 17.2 |
| summary | ledger | 5/6 | 5/6 | 0/6 | 200,348 | 0.0 | 8.0 |
| clm | ledger | 4/6 | 4/6 | 0/6 | 239,152 | 9.2 | 18.8 |
| clm_lessons | ledger | 2/6 | 3/6 | 1/6 | 197,408 | 8.5 | 20.2 |
| clm_code | ledger | 5/6 | 5/6 | 0/6 | 194,167 | 7.5 | 16.7 |
| summary | learned (3) | 13/18 | 15/18 | 2/18 | 257,210 | 0.0 | 8.7 |
| clm | learned (3) | 13/18 | 16/18 | 3/18 | 242,829 | 7.7 | 18.3 |
| clm_lessons | learned (3) | 13/18 | 17/18 | 4/18 | 235,894 | 12.0 | 23.1 |
| clm_code | learned (3) | 15/18 | 16/18 | 1/18 | 255,832 | 10.7 | 21.4 |
| summary | all | 18/24 | 20/24 | 2/24 | 242,995 | 0.0 | 8.5 |
| clm | all | 17/24 | 20/24 | 3/24 | 241,910 | 8.1 | 18.4 |
| clm_lessons | all | 15/24 | 20/24 | 5/24 | 226,272 | 11.1 | 22.3 |
| clm_code | all | 20/24 | 21/24 | 1/24 | 240,416 | 9.9 | 20.2 |

lessons: 9/18 changes accepted; code changes 0 (0 built, 0 accepted); rejected for lost clean runs 7, for tokens over 1.25x 5.
- r0: plugins kept none, 4 lessons
- r1: plugins kept none, 8 lessons
- r2: plugins kept none, 6 lessons

code: 9/18 changes accepted; code changes 8 (8 built, 5 accepted); rejected for lost clean runs 3, for tokens over 1.25x 9.
- r0: plugins kept ['ctx_note', 'ctx_trim'], 11 lessons
- r1: plugins kept ['ctxkeep'], 4 lessons
- r2: plugins kept ['ctx_trim'], 2 lessons
