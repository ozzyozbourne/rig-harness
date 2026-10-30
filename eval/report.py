"""Summarizes eval/run.py's results: python3 eval/report.py /tmp/eval-out"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import rate, read_jsonl  # noqa: E402


def avg(rs, k):
    return sum(r[k] for r in rs) / max(len(rs), 1)


out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/eval-out"
rows = read_jsonl(os.path.join(out, "results.jsonl"))
by = collections.defaultdict(list)
for r in rows:
    by[(r["family"], r["cond"])].append(r)

families = sorted({r["family"] for r in rows})
print("| Family | Task (base → cont) | Convention (base → cont) | Tool calls (base → cont) | Cost $ (base → cont) |")
print("|---|---|---|---|---|")
for fam in families:
    b, c = by[(fam, "baseline")], by[(fam, "continual")]
    cells = [f"{rate([r[key] for r in b])} → {rate([r[key] for r in c])}" for key in ("task_ok", "convention_ok")]
    cells.append(f"{avg(b, 'n_tool_calls'):.1f} → {avg(c, 'n_tool_calls'):.1f}")
    cells.append(f"{avg(b, 'cost'):.4f} → {avg(c, 'cost'):.4f}")
    print(f"| {fam} | " + " | ".join(cells) + " |")
print()
for cond in ("baseline", "continual"):
    rs = [r for r in rows if r["cond"] == cond and r["family"] in families]
    t = {k: [r[k] for r in rs if r[k] is not None] for k in ("task_ok", "convention_ok", "n_tool_calls", "cost", "seconds")}
    print(f"{cond}: task {sum(t['task_ok'])}/{len(t['task_ok'])}, convention {sum(t['convention_ok'])}/{len(t['convention_ok'])}, "
          f"avg tool calls {sum(t['n_tool_calls'])/len(t['n_tool_calls']):.1f}, total cost ${sum(t['cost']):.3f}, "
          f"avg {sum(t['seconds'])/len(t['seconds']):.0f}s")
env = [r for r in rows if r["family"] == "app_env"]
for cond in ("baseline", "continual"):
    rs = [r for r in env if r["cond"] == cond]
    print(f"app_env {cond}: first test run had APP_ENV {sum(r['first_test_has_app_env'] for r in rs)}/{len(rs)}, "
          f"failed test runs before APP_ENV avg {sum(r['failed_test_runs_before_app_env'] for r in rs)/max(len(rs),1):.1f}")
learn = read_jsonl(os.path.join(out, "learn.jsonl"))
print(f"learning: {len(learn)} episodes, cost ${sum(l['cost'] for l in learn):.3f}, "
      f"episodes that added/changed lessons {sum(l['harness_calls'] > 0 for l in learn)}/{len(learn)}")
