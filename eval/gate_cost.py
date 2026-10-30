"""Cost check for the gate: catch lessons that cost time without failing a check.

  EVAL_MODEL=opencode-go/deepseek-v4.1-flash python3 eval/gate_cost.py --out /tmp/gate-cost
  python3 eval/gate_cost.py --report /tmp/gate-cost

Bases are the 5 clean memories of Experiment A's ungated arm. Each base runs
the regression suite twice (3 held-out tasks + CI of all eight learning
tasks), recording per item whether it passed, its failed shell commands
(`[exit code N]`, N > 0) and its tool calls. Each candidate (no_env, plus the
two good lessons as controls) runs the suite once, then once more.

The rule, fixed before the run:
- pass check: the strict gate of eval/experiment_a.py;
- cost check: an item is slower when its failed commands exceed the worse of
  the base's two runs by more than SLACK. A candidate is rejected when some
  item is slower in both of its runs, or when its suite total of failed
  commands exceeds the worse base total by more than TOTAL_SLACK in both runs.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import experiment_a as ea  # noqa: E402
from gate_test import CANDIDATES, FAMS, for_each_base  # noqa: E402
from run import log, parallel, read_jsonl  # noqa: E402

SLACK = 1
TOTAL_SLACK = 2
TESTED = ["no_env", "verify", "read_first"]


def suite(out, tag, state):
    """Every item of the full regression suite with a copy of `state`, with what it cost."""
    return ea.suite(out, tag, state, FAMS, item=ea.measure_item)


def total(s):
    """Failed shell commands over a whole suite run."""
    return sum(v["failed"] for v in s.values())


def mean(xs):
    return sum(xs) / max(len(xs), 1)


def judge(base_runs, cand_runs):
    """The pass and cost verdicts of a candidate's two runs against the base's two."""
    worst = {k: max(b[k]["failed"] for b in base_runs) for k in base_runs[0]}
    passed_base = {k: any(b[k]["pass"] for b in base_runs) for k in base_runs[0]}
    lost = [k for k in worst if passed_base[k] and not any(c[k]["pass"] for c in cand_runs)]
    slower = [k for k in worst if all(c[k]["failed"] > worst[k] + SLACK for c in cand_runs)]
    worst_total = max(total(b) for b in base_runs)
    total_slower = all(total(c) > worst_total + TOTAL_SLACK for c in cand_runs)
    return {"lost": lost, "slower_items": slower, "total_slower": total_slower,
            "pass_accept": not lost, "cost_accept": not slower and not total_slower}


def one_base(out, b, base):
    cands = {name: os.path.join(out, "states", f"b{b}-{name}.json") for name in TESTED}
    for name, cand in cands.items():
        ea.add_lesson(base, cand, CANDIDATES[name][1])
    # The base's two runs and each candidate's two, all at once.
    runs = [(None, base)] * 2 + [(name, cands[name]) for name in TESTED for _ in (0, 1)]
    done = parallel([lambda i=i, n=n, s=s: suite(out, f"b{b}-{n or 'base'}-{i % 2}", s) for i, (n, s) in enumerate(runs)])
    base_runs = done[:2]
    for k, name in enumerate(TESTED):
        cand_runs = done[2 + 2 * k: 4 + 2 * k]
        v = judge(base_runs, cand_runs)
        row = {"base": b, "candidate": name, "harmful": CANDIDATES[name][0],
               "base_runs": base_runs, "cand_runs": cand_runs, **v}
        log(out, "cost.jsonl", row)
        print(f"[b{b}] {name}: pass={v['pass_accept']} cost={v['cost_accept']} slower={v['slower_items']} "
              f"failed base={[total(r) for r in base_runs]} cand={[total(r) for r in cand_runs]}", flush=True)


def report(out):
    rows = read_jsonl(os.path.join(out, "cost.jsonl"))
    print("| Lesson | Harmful | Pass check accepted | Pass + cost check accepted | Failed commands, base → candidate (mean of runs) | ci:app_env failed commands, base → candidate |")
    print("|---|---|---|---|---|---|")
    for name in TESTED:
        rs = [r for r in rows if r["candidate"] == name]
        b = mean([total(x) for r in rs for x in r["base_runs"]])
        c = mean([total(x) for r in rs for x in r["cand_runs"]])
        be = mean([x["ci:app_env"]["failed"] for r in rs for x in r["base_runs"]])
        ce = mean([x["ci:app_env"]["failed"] for r in rs for x in r["cand_runs"]])
        both = sum(r["pass_accept"] and r["cost_accept"] for r in rs)
        print(f"| {name} | {'yes' if CANDIDATES[name][0] else 'no'} | {sum(r['pass_accept'] for r in rs)}/{len(rs)} | "
              f"{both}/{len(rs)} | {b:.1f} → {c:.1f} | {be:.1f} → {ce:.1f} |")
    print()
    for r in rows:
        print(f"b{r['base']} {r['candidate']}: slower items {r['slower_items'] or '-'}, total slower {r['total_slower']}, lost {r['lost'] or '-'}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="/tmp/gate-cost")
    p.add_argument("--report")
    a = p.parse_args()
    if a.report:
        report(a.report)
        return
    for_each_base(a.out, one_base)


if __name__ == "__main__":
    main()
