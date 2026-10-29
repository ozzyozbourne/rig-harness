"""Summarizes eval/run.py's results: python3 eval/report.py /tmp/eval-out"""
import collections
import json
import os
import sys

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/eval-out"
rows = [json.loads(l) for l in open(os.path.join(out, "results.jsonl"))]
by = collections.defaultdict(list)
for r in rows:
    by[(r["family"], r["cond"])].append(r)


def rate(xs):
    xs = [x for x in xs if x is not None]
    return f"{sum(xs)}/{len(xs)}" if xs else "n/a"


families = sorted({r["family"] for r in rows})
print("| Family | Task (base → cont) | Convention (base → cont) | Tool calls (base → cont) | Cost $ (base → cont) |")
print("|---|---|---|---|---|")
tot = collections.defaultdict(lambda: collections.defaultdict(list))
for fam in families:
    b, c = by[(fam, "baseline")], by[(fam, "continual")]
    cells = []
    for key in ("task_ok", "convention_ok"):
        cells.append(f"{rate([r[key] for r in b])} → {rate([r[key] for r in c])}")
    avg = lambda rs, k: sum(r[k] for r in rs) / max(len(rs), 1)
    cells.append(f"{avg(b, 'n_tool_calls'):.1f} → {avg(c, 'n_tool_calls'):.1f}")
    cells.append(f"{avg(b, 'cost'):.4f} → {avg(c, 'cost'):.4f}")
    print(f"| {fam} | " + " | ".join(cells) + " |")
    for cond, rs in (("baseline", b), ("continual", c)):
        for r in rs:
            for k in ("task_ok", "convention_ok", "n_tool_calls", "cost", "seconds"):
                if r[k] is not None:
                    tot[cond][k].append(r[k])
print()
for cond in ("baseline", "continual"):
    t = tot[cond]
    print(f"{cond}: task {sum(t['task_ok'])}/{len(t['task_ok'])}, convention {sum(t['convention_ok'])}/{len(t['convention_ok'])}, "
          f"avg tool calls {sum(t['n_tool_calls'])/len(t['n_tool_calls']):.1f}, total cost ${sum(t['cost']):.3f}, "
          f"avg {sum(t['seconds'])/len(t['seconds']):.0f}s")
env = [r for r in rows if r["family"] == "app_env"]
for cond in ("baseline", "continual"):
    rs = [r for r in env if r["cond"] == cond]
    print(f"app_env {cond}: first test run had APP_ENV {sum(r['first_test_has_app_env'] for r in rs)}/{len(rs)}, "
          f"failed test runs before APP_ENV avg {sum(r['failed_test_runs_before_app_env'] for r in rs)/max(len(rs),1):.1f}")
learn = [json.loads(l) for l in open(os.path.join(out, "learn.jsonl"))]
print(f"learning: {len(learn)} episodes, cost ${sum(l['cost'] for l in learn):.3f}, "
      f"episodes that added/changed lessons {sum(l['harness_calls'] > 0 for l in learn)}/{len(learn)}")
