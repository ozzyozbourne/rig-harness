| Family | Task (base → cont) | Convention (base → cont) | Tool calls (base → cont) | Cost $ (base → cont) |
|---|---|---|---|---|
| answer_format | 3/3 → 3/3 | 0/3 → 3/3 | 4.0 → 8.7 | 0.0007 → 0.0019 |
| app_env | 3/3 → 3/3 | n/a → n/a | 12.0 → 9.3 | 0.0051 → 0.0046 |
| app_error | 3/3 → 3/3 | 1/3 → 3/3 | 6.0 → 9.7 | 0.0011 → 0.0018 |
| changelog | 3/3 → 3/3 | 0/3 → 3/3 | 5.0 → 8.7 | 0.0008 → 0.0014 |
| logging | 3/3 → 3/3 | 2/3 → 3/3 | 8.7 → 9.3 | 0.0015 → 0.0018 |
| regression_test | 3/3 → 3/3 | 0/3 → 2/3 | 5.3 → 8.7 | 0.0006 → 0.0012 |
| spdx_header | 3/3 → 3/3 | 0/3 → 3/3 | 4.0 → 8.7 | 0.0007 → 0.0016 |
| type_hints | 3/3 → 3/3 | 0/3 → 3/3 | 3.7 → 10.0 | 0.0006 → 0.0028 |

baseline: task 24/24, convention 3/21, avg tool calls 6.1, total cost $0.033, avg 11s
continual: task 24/24, convention 20/21, avg tool calls 9.1, total cost $0.052, avg 20s
app_env baseline: first test run had APP_ENV 0/3, failed test runs before APP_ENV avg 1.3
app_env continual: first test run had APP_ENV 3/3, failed test runs before APP_ENV avg 0.0
learning: 24 episodes, cost $0.294, episodes that added/changed lessons 24/24
