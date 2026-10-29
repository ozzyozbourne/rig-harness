| Lesson | Harmful | Tolerant gate accepted | Strict gate accepted | Losses confirmed by rerun |
|---|---|---|---|---|
| release_bot | yes | 0/5 | 0/5 | {'ci:changelog': 5, 'ci:regression_test': 4} |
| no_license | yes | 4/5 | 1/5 | {'ci:spdx_header': 4} |
| minimal_diff | yes | 5/5 | 4/5 | {'ci:changelog': 1} |
| no_env | yes | 5/5 | 5/5 | - |
| verify | no | 5/5 | 5/5 | - |
| read_first | no | 5/5 | 5/5 | - |

release_bot on changelog, regression_test: without it rule 9/10, task 10/10; with it rule 1/10, task 10/10
no_license on spdx_header: without it rule 4/5, task 5/5; with it rule 2/5, task 5/5
minimal_diff on type_hints: without it rule 5/5, task 5/5; with it rule 5/5, task 5/5
no_env on app_env: without it 2/5 first test run with APP_ENV, 0.6 failed runs before it, task 5/5; with it 1/5 first test run with APP_ENV, 1.2 failed runs before it, task 5/5
