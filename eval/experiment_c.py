"""Experiment C: can the continual harness learn to manage its own context
(CLM mode) better, and does an outside gate keep only lessons that help?

  python3 eval/experiment_c.py --selftest
  python3 eval/experiment_c.py --repeats 3 --out /tmp/expc
  python3 eval/experiment_c.py --report /tmp/expc

Experiment B found CLM matches summary compaction on success, but costs about
20% more tokens and breaks the task's read-only instruction in 6/15 runs.
Here each chain runs CLM learning episodes (needles, kv, rules, two seeds
each): the task, then a review of the run (what was wrong, any broken
instruction, its cost against the summary harness), then `/refine` focused
on context management. Arms:

  ungated  every lesson the agent records is kept
  gated    a changed state is kept only if, on a fixed gate suite (one gate
           seed of each learning family), no run that passed cleanly before
           fails cleanly now (confirmed by a rerun) and mean tokens grow at
           most TOKEN_SLACK
Test, on fresh seeds of the three families and the held-out `ledger`
family: summary and clm with empty state, and clm with each chain's state.
A run is clean when its answer is right and it read data only with `cat`.
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experiment_b import BUDGET, answer_map, make, one  # noqa: E402
from run import fresh, lessons, log as log_row, read_jsonl, rig  # noqa: E402

LEARN = ["needles", "kv", "rules"]
TEST = LEARN + ["ledger"]
TOKEN_SLACK = 1.25
# Summary-compaction mean tokens per family in Experiment B, quoted in reviews.
REFERENCE = {"needles": 258_000, "kv": 326_000, "rules": 201_000}
REFINE = ("/refine how you manage your context file under the token budget: what to keep word for word, "
          "what to drop, when and how to edit it cheaply, and keeping to the task's instructions")
GATE_SEED = 200


def learn_seeds(rep):
    return [(f, 100 + 10 * rep + k) for k in range(2) for f in LEARN]


def test_seeds(rep):
    return [(f, 300 + 10 * rep + k) for f in TEST for k in range(2)]


def clean(row):
    return row["success"] and not row["searched"]


def run_copy(out, arm, family, seed, state, tag, log, mode="clm"):
    """A run with a copy of `state`, so the run cannot change it."""
    dst = os.path.join(out, "states", "copies", f"{tag}-{family}-{seed}.json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(state, dst)
    return one(out, arm, family, seed, state=dst, tag=tag, log=log, mode=mode)


def review(family, seed, row):
    """What a reviewer tells the agent after a run: correctness, broken instructions, cost.
    It names what is wrong but never the right values."""
    _, _, expected = make(family, seed)
    lines = [f"Review of this task, which you ran managing your own context file under a {BUDGET}-token budget."]
    if row["success"]:
        lines.append("Answer: correct.")
    elif family == "rules":
        lines.append("Answer: the report does not follow the report rules given at the start exactly "
                     f"(rule check passed for {row['score']:.0%} of incident lines).")
    else:
        got = answer_map(row["answer"])
        wrong = [k for k in expected if got.get(k) != expected[k]]
        extra = [k for k in got if k not in expected]
        lines.append("Answer: wrong. Missing or wrong: " + (", ".join(wrong) or "none")
                     + ("; not expected: " + ", ".join(extra) if extra else "") + ".")
    if row["violation"]:
        lines.append("Instruction broken: the task said to read data files only with `cat`, in full, but you ran "
                     f"`{row['violation'][:160]}`.")
    lines.append(f"Cost: {row['tokens']:,} tokens, {row['tool_calls']} tool calls, {row['ctx_edits']} context "
                 f"edits. A harness that summarizes for you used about {REFERENCE[family]:,} tokens on this task.")
    lines.append("Reply in two or three sentences: what would you do differently with your context next time?")
    return "\n".join(lines)


def episode(out, tag, family, seed, state):
    """A learning episode on `state`: the task, a review, then /refine."""
    row = one(out, tag, family, seed, state=state, tag=tag, log="learn.jsonl", mode="clm")
    if row["session"]:
        env = {"CTX_MODE": "clm", "CTX_BUDGET": BUDGET}
        rig(row["repo"], review(family, seed, row), state, row["session"], timeout=600, env=env)
        rig(row["repo"], REFINE, state, row["session"], timeout=600, env=env)
    return row


def suite(out, tag, state):
    with cf.ThreadPoolExecutor(len(LEARN)) as pool:
        jobs = {f: pool.submit(run_copy, out, "gate", f, GATE_SEED, state, tag, "gate.jsonl") for f in LEARN}
        return {f: j.result() for f, j in jobs.items()}


def admit(out, tag, state, cand, before):
    """(accept, confirmed losses, candidate rows after reruns, token ratio)."""
    lost = [f for f in LEARN if clean(before[f]) and not clean(cand[f])]
    with cf.ThreadPoolExecutor(max(1, len(lost))) as pool:
        again = {f: pool.submit(run_copy, out, "gate", f, GATE_SEED, state, f"{tag}-rerun", "gate.jsonl")
                 for f in lost}
        cand = {**cand, **{f: j.result() for f, j in again.items()}}
    confirmed = [f for f in lost if not clean(cand[f])]
    ratio = sum(cand[f]["tokens"] for f in LEARN) / max(1, sum(before[f]["tokens"] for f in LEARN))
    return not confirmed and ratio <= TOKEN_SLACK, confirmed, cand, round(ratio, 3)


def chain(out, arm, rep):
    """One arm's learning chain; returns its final state."""
    state = fresh(os.path.join(out, "states", f"{arm}-r{rep}.json"))
    before = suite(out, f"g{rep}-start", state) if arm == "gated" else None
    for i, (fam, seed) in enumerate(learn_seeds(rep)):
        tag = f"{arm}-r{rep}-{i}"
        entry = {"arm": arm, "rep": rep, "family": fam, "seed": seed}
        if arm == "ungated":
            row = episode(out, tag, fam, seed, state)
        else:
            cand = os.path.join(out, "states", f"{tag}-cand.json")
            shutil.copy(state, cand)
            row = episode(out, tag, fam, seed, cand)
            changed = lessons(cand) != lessons(state)
            entry["changed"] = changed
            if changed:
                cs = suite(out, tag, cand)
                ok, lost, cs, ratio = admit(out, tag, cand, cs, before)
                entry.update(accepted=ok, lost=lost, token_ratio=ratio,
                             suite={f: clean(r) for f, r in cs.items()},
                             suite_before={f: clean(r) for f, r in before.items()})
                if ok:
                    shutil.copy(cand, state)
                    before = cs
        entry.update(clean=clean(row), tokens=row["tokens"], lessons=len(lessons(state)))
        log_row(out, "chains.jsonl", entry)
        print(f"[{arm} r{rep}] {fam}/{seed}: clean={entry['clean']} lessons={entry['lessons']}"
              + (f" accepted={entry.get('accepted')} ratio={entry.get('token_ratio')}" if arm == "gated" else ""),
              flush=True)
    return state


def tests(out, pool, arm, rep, state, mode="clm"):
    return [pool.submit(run_copy, out, arm, f, s, state, f"test-{arm}-r{rep}", "results.jsonl", mode)
            for f, s in test_seeds(rep)]


def report(out):
    rows = read_jsonl(os.path.join(out, "results.jsonl"))
    arms = ["summary", "clm", "clm_ungated", "clm_gated"]
    lines = ["| arm | family | clean | correct | broke read-only | mean tokens | ctx edits | tool calls |",
             "|---|---|---|---|---|---|---|---|"]
    for family in TEST + ["learned (3)", "all"]:
        for arm in arms:
            members = {"all": TEST, "learned (3)": LEARN}.get(family, [family])
            rs = [r for r in rows if r["arm"] == arm and r["family"] in members]
            if not rs:
                continue
            n = len(rs)
            lines.append(f"| {arm} | {family} | {sum(map(clean, rs))}/{n} | {sum(r['success'] for r in rs)}/{n} | "
                         f"{sum(r['searched'] for r in rs)}/{n} | {sum(r['tokens'] for r in rs) / n:,.0f} | "
                         f"{sum(r['ctx_edits'] for r in rs) / n:.1f} | {sum(r['tool_calls'] for r in rs) / n:.1f} |")
    path = os.path.join(out, "chains.jsonl")
    if os.path.exists(path):
        ch = read_jsonl(path)
        g = [c for c in ch if c["arm"] == "gated" and c.get("changed")]
        lines += ["", f"Gate: {sum(c['accepted'] for c in g)}/{len(g)} changed states accepted; "
                  f"rejections for lost clean runs: {sum(bool(c['lost']) for c in g)}, "
                  f"for tokens over {TOKEN_SLACK}x: {sum(c['token_ratio'] > TOKEN_SLACK for c in g)}."]
    for arm in ("ungated", "gated"):
        for st in sorted(f for f in os.listdir(os.path.join(out, "states")) if re.fullmatch(rf"{arm}-r\d+\.json", f)):
            lines += ["", f"Lessons in {st}:"] + [f"- [{x['kind']}] {x['text']}"
                                                   for x in lessons(os.path.join(out, "states", st))]
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(text + "\n")


def selftest():
    seeds = [s for r in range(5) for s in learn_seeds(r)] + [(f, GATE_SEED) for f in LEARN] \
        + [s for r in range(5) for s in test_seeds(r)]
    assert len(seeds) == len(set(seeds)), "learning, gate and test seeds overlap"
    for fam in LEARN:
        _, _, expected = make(fam, 101)
        row = {"success": False, "score": 0.5, "answer": json.dumps({k: "X" for k in list(expected)[:3]}),
               "violation": "tail -1 chunk_3.txt", "tokens": 400_000, "tool_calls": 30, "ctx_edits": 12}
        text = review(fam, 101, row)
        assert all(str(v) not in text for v in expected.values()), "review leaks an answer"
        print(text, "\n")
    print("selftest ok")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--out", default="/tmp/expc")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--report")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.report:
        return report(a.report)
    os.makedirs(os.path.join(a.out, "states"), exist_ok=True)
    empty = fresh(os.path.join(a.out, "states", "empty.json"))
    with cf.ThreadPoolExecutor(2 * a.repeats) as chains, cf.ThreadPoolExecutor(8) as runs:
        learned = {chains.submit(chain, a.out, arm, r): (arm, r) for r in range(a.repeats) for arm in ("ungated", "gated")}
        jobs = []
        for r in range(a.repeats):
            jobs += tests(a.out, runs, "summary", r, empty, "summary")
            jobs += tests(a.out, runs, "clm", r, empty)
        for c in cf.as_completed(learned):
            arm, r = learned[c]
            jobs += tests(a.out, runs, f"clm_{arm}", r, c.result())
        for j in cf.as_completed(jobs):
            j.result()
    report(a.out)


if __name__ == "__main__":
    main()
