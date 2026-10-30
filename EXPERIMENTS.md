# Experiments A–C: summary

All three ran on DeepSeek V4.1 Flash (`opencode-go/deepseek-v4.1-flash`) in
this harness. Earlier GLM-5.3-Flash evals (rules followed 3/21 → 20/21,
self-modification 4/4) are in the README. Raw data is in `eval/results/`.

## Short version

| Experiment | Question | Answer |
|---|---|---|
| A | Can the harness learn unstated team rules from CI feedback alone, and can a gate keep bad lessons out? | **Yes, it learns** (rules 11/35 → 33/35). A gate blocks harm **only when its regression suite exercises that harm**. |
| B | Does an agent that edits its own context (CLM, arXiv 2609.37725) beat summary compaction at a 32K budget? | **No: it ties.** It needs more tool calls and breaks instructions somewhat more often. |
| C | Can the harness learn to do CLM better? | **No.** Ungated lessons made it worse (+38% tokens). The gate kept costs flat but found nothing that helped. |

The overall picture: learning *facts and rules* works well. Learning
*procedural skill* (how to manage context) through prompt-level lessons did
not work with this model. An outside gate is a useful brake, but only as
good as the cases it tests.

## Experiment A: learning from CI feedback, with a gate

**Setup** (`eval/experiment_a.py`, 5 repeats): 8 task families, each with a
hidden team rule (changelog entry, regression test, SPDX header, logging,
`Risk:` line, `APP_ENV`, type hints, `AppError`). In learning episodes
nobody states the rule. The agent gets a CI report (pass/fail plus a
linter-style line), then `/refine`. Test tasks run in fresh repos that never
mention the rule.

| arm | tasks solved | rules followed | avg tokens |
|---|---|---|---|
| baseline (empty memory) | 40/40 | 11/35 | 21K |
| ungated learning | 40/40 | **33/35** | 40K |
| ungated + 1 planted harmful lesson | 40/40 | 25/35 | 37K |
| gated (tolerant gate) | 40/40 | 27/35 | 39K |

- **Learning works.** All 8 rules ended up in memory in 5/5 chains. The
  cost is about 2x tokens per task from the larger prompt and extra checks.
- **Poison works too.** One plausible harmful lesson took its two
  contradicted rules from 9/10 to 1/10.
- **Tolerant gate** (allows one lost regression item): let the poison
  through 1/5. It also cost some good learning (27/35 against 33/35).

**Strict gate follow-up** (`eval/gate_test.py`): reject any regression-suite
loss that a rerun confirms. Tested with 4 harmful and 2 good lessons.

| lesson | harmful | tolerant accepted | strict accepted |
|---|---|---|---|
| release_bot (no changelog / regression tests) | yes | 0/5 | 0/5 |
| no_license (skip SPDX headers) | yes | 4/5 | **1/5** |
| minimal_diff | yes, but caused no measurable harm | 5/5 | 4/5 |
| no_env (don't set APP_ENV) | yes | 5/5 | **5/5** (missed) |
| verify, read_first (good) | no | 10/10 | 10/10 |

**Cost check** (`eval/gate_cost.py`): adding a failed-command count to the
gate still accepted no_env 5/5, because the regression suite never runs a
task where its harm shows.

**Takeaway:** a gate catches only harm that its suite exercises. To make it
strong, each learned rule needs sibling test tasks.

## Experiment B: self-managed context (CLM)

**Setup** (`plugins/ctx_mode`, `eval/experiment_b.py`, 5 repeats × 3
families, 32K budget, about 50–70K tokens of tool output per task):

- `summary`: rig's default compaction, set to trigger at the budget.
- `keep_last`: the task plus the newest half of the budget, no summary.
- `clm`: the conversation is mirrored to `.clm/<agent>.md`. The agent edits
  it, and the edits become its context before each call. Summary is only a
  fallback 8K past the budget.

Families: `needles` (a chain of 10 files, report every key), `kv` (a store
updated over 8 resumed turns), `rules` (an exact report format, then a chain
of 8 files).

| arm | correct | clean (correct, no broken instruction) | avg tokens | summaries | ctx edits |
|---|---|---|---|---|---|
| summary | 14/15 | 13/15 | 261K | 2.0 | 0 |
| keep_last | 3/15 | 2/15 | 576K | 0 | 7.8 |
| clm | 14/15 | 10/15 | 317K | 0 | 10.4 |

- **CLM works on a small model.** It never needed the fallback summary.
- **It ties summary on correctness**, at about 20% more tokens and 2–3x the
  tool calls. It broke the "read only with `cat`" instruction in 4/15 runs,
  against 1/15 for summary.
- **keep_last is the floor:** without a summary, early facts are lost.

## Experiment C: learning to do CLM better

**Setup** (`eval/experiment_c.py`, 3 chains per arm, 6 episodes each): the
CLM task, then a review (right or wrong without the answers, any broken
instruction, token cost against summary), then `/refine` focused on context
management.

- Ungated: keeps every lesson.
- Gated: keeps a change only if a fixed suite loses no clean run (confirmed
  by a rerun) and uses at most 1.25x the tokens.

Testing used 24 fresh-seed runs per arm, including the held-out `ledger`
family.

| arm | clean | correct | broke read-only | avg tokens | ctx edits |
|---|---|---|---|---|---|
| summary (no CLM) | 18/24 | 21/24 | 3/24 | 256K | 0 |
| clm, empty state | 18/24 | 21/24 | 3/24 | 256K | 7.8 |
| clm, ungated lessons | 17/24 | 22/24 | 5/24 | 354K | 18.8 |
| clm, gated lessons | 18/24 | 21/24 | 4/24 | 269K | 11.7 |

- **No learned arm beat CLM with an empty state**, on the trained families
  or on the held-out one.
- **Ungated chains wrote 8–9 lessons**, mostly "edit the context file more
  often": +38% tokens and 2.4x edits.
- **The gate rejected 7/18 changes** (5 for tokens, 2 for lost clean runs)
  and held cost within 5% of the empty state.
- **Lessons were often correct in text** ("never pipe a data file into
  `tail`") but did not change behavior.

## Measurement notes

- The read-only checker scans every shell command in the session log. It
  flags filters (`tail`, `wc`, `grep`...) run on data files and scripts that
  open a data file by name, and ignores scripts that only edit the context
  file. A first version over-counted CLM violations in B (6/15, corrected to
  4/15). Each result row stores its verdict and its session's tokens, so the
  reports rebuild from the repo alone.
- Each run's usage is the sum of its own `turn_end` events. The `exit`
  event repeats the session's running total on resumed turns; that inflated
  the episode token and cost counts logged by `eval/run.py` and Experiment
  A's chains (no reported number used them).
- A failed shell command is one whose last line, the shell tool's status
  line, is a non-zero exit, a kill or a timeout. Before, `[exit code N]`
  anywhere in the output counted, including in a test's own output.
- `ctx_mode` measures tokens as rig's compaction does (the provider's last
  reported usage plus an estimate of newer messages), so all three arms of B
  use the same measure of the budget. B and C ran with an earlier
  four-characters-a-token estimate for `keep_last` and `clm`.
- Compactions, CLM edits and `keep_last` cuts are counted from the asides
  each leaves in the session log. B and C counted `ctx_mode`'s log records,
  which is about two per edit.
- `rig()` caps concurrent rig processes for the whole script (`EVAL_SLOTS`,
  default 8), and the gate scripts run independent suites side by side.
- Sample sizes are small (15–40 runs per cell). Differences of 1–2 runs are
  noise.

## What this suggests next

1. **Gate suites with sibling tasks per learned rule**, so a gate can see the
   harm of lessons like no_env (the gap Experiment A found).
2. **Enforce procedural constraints in code, not lessons.** C showed that
   text lessons don't change tool-use habits. For example, a CLM helper tool
   that prunes in one call, or a shell policy that blocks filters on
   declared data files.
3. **Retest CLM on a stronger model or a tighter budget** (8–16K), where
   summary compaction should lose more facts and self-management has more
   room to win.
