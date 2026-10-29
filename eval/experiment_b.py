"""Experiment B: does an agent that manages its own context (CLM, arXiv
2609.37725) keep what a long task needs better than rig's summary
compaction or a plain keep-last cut, at the same token budget?

  python3 eval/experiment_b.py --selftest
  python3 eval/experiment_b.py --repeats 5 --out /tmp/expb
  python3 eval/experiment_b.py --report /tmp/expb

Arms (the `ctx_mode` plugin, chosen per run by CTX_MODE; CTX_BUDGET=32000):
  summary    rig's default compaction, acting at the budget
  keep_last  the task and the newest half budget of messages are kept
  clm        the agent edits its own context file; summary is the fallback
Families, each about 40-50K tokens of tool output through a 32K budget:
  needles    a chain of 10 files, each naming the next and holding one
             key; report all 10
  kv         a key-value store set and updated over 8 resumed turns, kept
             in context only; report the final store
  rules      exact report rules given first, then a chain of 8 files
             with one incident each; the report must follow every rule
  ledger     (held out, used by experiment_c) a chain of 8 files with two
             transactions each; report every final balance
"""

import argparse
import concurrent.futures as cf
import glob
import json
import os
import random
import re
import string
import sys

sys.path.insert(0, os.path.dirname(__file__))
from run import EMPTY, make_repo, rig  # noqa: E402

ARMS = ["summary", "keep_last", "clm"]
FAMILIES = ["needles", "kv", "rules"]
BUDGET = os.environ.get("CTX_BUDGET", "32000")
WORDS = "alpha beta gamma delta epsilon zeta theta kappa lambda sigma omega river stone cloud ember".split()
READ_ONLY = ("Read files only with `cat <file>`, in full, one file per command; do not use grep, rg, awk, "
             "sed, head, tail, or scripts to read or search the data files.")


def filler(rng, lines):
    return [f"{i:04d} " + " ".join(rng.choice(WORDS) for _ in range(14)) for i in range(lines)]


def code(rng):
    return "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(8))


def chained(rng, lines, length):
    """Files that each hold one of `lines` and, at another random spot, the
    name of the next file, so they can only be read one after another."""
    names = [f"{code(rng).lower()}.log" for _ in lines]
    files = {}
    for i, (name, line) in enumerate(zip(names, lines)):
        body = filler(rng, length)
        a, b = sorted(rng.sample(range(length // 10, length - length // 10), 2))
        pair = [line, f"     NEXT FILE: {names[i + 1] if i + 1 < len(names) else 'none'}"]
        rng.shuffle(pair)
        files[name] = "\n".join(body[:a] + [pair[0]] + body[a:b] + [pair[1]] + body[b:]) + "\n"
    return files


def make(family, seed):
    """(files, turns, expected) for one run of `family`."""
    rng = random.Random(f"{family}-{seed}")
    if family == "needles":
        keys = {f"KEY_{i}": code(rng) for i in range(10)}
        files = chained(rng, [f"     KEY_{i} = {v}" for i, v in enumerate(keys.values())], 300)
        task = (f"Read the file chain that starts at {next(iter(files))}. {READ_ONLY} Each file holds one line "
                "`KEY_<n> = <value>` and one line `NEXT FILE: <name>` naming the next file to read (the last "
                "file says `NEXT FILE: none`). After reading the whole chain, answer with only a JSON object "
                "mapping each KEY_<n> to its value.")
        return files, [task], keys
    if family == "kv":
        store = {f"k{i}": code(rng) for i in range(6)}
        files = {f"chunk_{i}.txt": "\n".join(filler(rng, 320)) + "\n" for i in range(7)}
        turns = [("We are testing your memory. Keep a key-value store in your context only: do not write it to "
                  "any file (your context file, if you have one, is part of your context and is allowed). "
                  f"{READ_ONLY} Set: " + ", ".join(f"{k}={v}" for k, v in store.items())
                  + ". Then cat chunk_0.txt and tell me its last line number. Reply briefly.")]
        n = len(store)
        for t in range(1, 7):
            ops = []
            for _ in range(2):
                if rng.random() < 0.3 and len(store) > 3:
                    k = rng.choice(sorted(store))
                    del store[k]
                    ops.append(f"delete {k}")
                else:
                    k = rng.choice(sorted(store)) if rng.random() < 0.5 else f"k{n}"
                    n += k == f"k{n}"
                    store[k] = code(rng)
                    ops.append(f"set {k}={store[k]}")
            turns.append(f"Store update: {'; '.join(ops)}. Then cat chunk_{t}.txt and tell me its last line "
                         "number. Reply briefly.")
        turns.append("Answer with only the current store as a JSON object of key to value.")
        return files, turns, store
    if family == "rules":
        incidents = {f"inc-{code(rng).lower()[:5]}": s for s in rng.sample(range(1, 100), 8)}
        files = chained(rng, [f"     INCIDENT {k} severity={v}" for k, v in incidents.items()], 300)
        rules = ("Report rules, follow every one exactly: (1) the first line is `REPORT v7`; (2) then one line per "
                 "incident as `<ID> | <severity>` with the ID in UPPERCASE, sorted by severity, highest first; "
                 "(3) then a line `TOTAL <sum of severities>`; (4) the last line is `-- end of report --`; "
                 "(5) nothing else, no code fences.")
        task = (f"{rules} Now read the file chain that starts at {next(iter(files))}. {READ_ONLY} Each file "
                "holds one line `INCIDENT <id> severity=<n>` and one line `NEXT FILE: <name>` naming the next "
                "file (the last says `NEXT FILE: none`). After reading the whole chain, write the report.")
        return files, [task], incidents
    if family == "ledger":
        accounts = [f"acct-{code(rng).lower()[:4]}" for _ in range(4)]
        balance = dict.fromkeys(accounts, 0)
        lines = []
        for _ in range(8):
            txs = []
            for _ in range(2):
                a, n = rng.choice(accounts), rng.choice([-1, 1]) * rng.randint(5, 95)
                balance[a] += n
                txs.append(f"     TX {a} {n:+d}")
            lines.append("\n".join(txs))
        files = chained(rng, lines, 300)
        task = (f"Read the file chain that starts at {next(iter(files))}. {READ_ONLY} Each file holds two lines "
                "`TX <account> <signed amount>` and one line `NEXT FILE: <name>` naming the next file (the last "
                "says `NEXT FILE: none`). Every account starts at 0. After reading the whole chain, answer with only "
                "a JSON object mapping each account to its final balance as an integer.")
        return files, [task], {a: str(b) for a, b in balance.items()}
    raise ValueError(family)


def json_object(answer):
    m = re.search(r"\{.*\}", answer, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def grade(family, answer, expected):
    """(score in [0, 1], success)."""
    if family in ("needles", "kv", "ledger"):
        got = {str(k): str(v) for k, v in json_object(answer).items()}
        keys = set(expected) | set(got)
        right = sum(1 for k in keys if got.get(k) == expected.get(k))
        return right / len(keys), got == expected
    lines = [l.strip() for l in answer.strip().splitlines() if l.strip()]
    order = sorted(expected.items(), key=lambda kv: -kv[1])
    want = ["REPORT v7"] + [f"{k.upper()} | {s}" for k, s in order] + [f"TOTAL {sum(expected.values())}",
                                                                     "-- end of report --"]
    facts = sum(1 for k, s in expected.items() if f"{k.upper()} | {s}" in lines) / len(expected)
    return facts, lines == want


def session_counts(session):
    """(summary compactions, ctx_mode edits) in a session's logs."""
    home = os.environ.get("RIG_HOME", os.path.expanduser("~/.rig"))
    compactions = edits = 0
    for path in glob.glob(os.path.join(home, "sessions", session or "-", "logs", "*.jsonl")):
        with open(path) as f:
            for line in f:
                if '"kind":"rig.compaction"' in line:
                    compactions += 1
                elif '"by":{"kind":{"plugin":"ctx_mode"}}' in line and '"type":"aside"' not in line:
                    edits += 1
    return compactions, edits


def session_usage(session):
    """(total, input, cached) tokens of a session's main agent, from its last
    saved usage; a resumed run's exit event repeats the running total."""
    home = os.environ.get("RIG_HOME", os.path.expanduser("~/.rig"))
    best = (0, 0, 0)
    for path in glob.glob(os.path.join(home, "sessions", session or "-", "logs", "*.jsonl")):
        with open(path) as f:
            for line in f:
                if '"key":"rig_basics::telemetry::usage::AgentUsage"' in line:
                    t = json.loads(line)["value"]["total"]["tokens"]
                    best = max(best, (t.get("total_tokens", 0), t.get("input_tokens", 0),
                                      t.get("cached_input_tokens", 0)))
    return best


FILTERS = {"grep", "rg", "awk", "sed", "head", "tail", "wc", "cut", "sort", "uniq", "od", "xxd", "strings", "less"}


def violation(commands, names):
    """The first command that runs a filter (`tail`, `wc`, `grep`...) on a data
    file (one of `names`), directly or through a pipe, or None. Heredoc bodies
    and quoted text are skipped: a script that edits the context file may
    mention data files in its note. A script that opens data files itself is
    not caught."""
    for cmd in commands:
        c = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2[ \t]*(?=\n|$)", "", cmd, flags=re.S)
        c = re.sub(r"'[^']*'|\"[^\"]*\"", "''", c)
        for pipeline in re.split(r";|&&|\|\||\n", c):
            segs = [seg.split() for seg in pipeline.split("|")]
            for i, words in enumerate(segs):
                if any(n in w for w in words for n in names) and any(
                        later[:1] and later[0] in FILTERS for later in segs[i:]):
                    return cmd
    return None


def shell_commands(calls):
    return [c["args"].get("command", "") for c in calls if c["name"] == "shell" and isinstance(c["args"], dict)]


def session_commands(session):
    """Every shell command a session's agents ran, from its logs."""
    home = os.environ.get("RIG_HOME", os.path.expanduser("~/.rig"))
    out = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("name") == "shell" and isinstance(x.get("arguments"), dict):
                out.append(x["arguments"].get("command", ""))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for path in glob.glob(os.path.join(home, "sessions", session or "-", "logs", "*.jsonl")):
        with open(path) as f:
            for line in f:
                if '"shell"' in line:
                    walk(json.loads(line))
    return out


def rescore(row):
    """`row` with its read-only check redone from its session's commands."""
    names = list(make(row["family"], row["rep"])[0])
    row["violation"] = violation(session_commands(row["session"]), names)
    row["searched"] = bool(row["violation"])
    return row


def one(out, arm, family, rep, state=None, tag=None, log="results.jsonl", mode=None):
    """One run of `family` seed `rep` in CTX_MODE `mode` (default `arm`), with the
    continual state file `state` (default a new empty one), which it may change."""
    files, turns, expected = make(family, rep)
    repo = os.path.join(out, "runs", tag or arm, family, f"r{rep}")
    make_repo(repo, files)
    if state is None:
        state = repo + ".state.json"
        with open(state, "w") as f:
            json.dump(EMPTY, f)
    env = {"CTX_MODE": mode or arm, "CTX_BUDGET": BUDGET}
    steps, session = [], None
    for turn in turns:
        r = rig(repo, turn, state, session, timeout=600, env=env)
        steps.append(r)
        session = session or r["session"]
        if not session:
            break
    answer = steps[-1]["answer"] if len(steps) == len(turns) else ""
    score, ok = grade(family, answer, expected)
    compactions, edits = session_counts(session)
    tokens, input_tokens, cached = session_usage(session)
    broke = violation([c for s in steps for c in shell_commands(s["tool_calls"])], list(files))
    row = {"arm": arm, "family": family, "rep": rep, "success": ok, "score": round(score, 3),
           "tokens": tokens, "input_tokens": input_tokens, "cached_tokens": cached,
           "tool_calls": sum(len(s["tool_calls"]) for s in steps), "compactions": compactions, "ctx_edits": edits,
           "searched": bool(broke), "violation": broke, "seconds": sum(s["seconds"] for s in steps),
           "errors": [s["error"] for s in steps if s["error"]], "session": session, "repo": repo, "answer": answer[-600:]}
    if log:
        with open(os.path.join(out, log), "a") as f:
            f.write(json.dumps(row) + "\n")
    print(f"[{arm}] {family} r{rep}: ok={ok} score={row['score']} tokens={row['tokens']} "
          f"compactions={compactions} edits={edits}", flush=True)
    return row


def report(out):
    rows = [json.loads(l) for l in open(os.path.join(out, "results.jsonl"))]
    for r in rows:
        r["tokens"], r["input_tokens"], r["cached_tokens"] = session_usage(r["session"])
        rescore(r)
    lines = ["| arm | family | success | mean score | mean tokens | mean uncached input | summaries | ctx edits | searched |",
             "|---|---|---|---|---|---|---|---|---|"]
    for family in FAMILIES + ["all"]:
        for arm in ARMS:
            rs = [r for r in rows if r["arm"] == arm and (family == "all" or r["family"] == family)]
            if not rs:
                continue
            n = len(rs)
            mean = lambda k: sum(r[k] for r in rs) / n  # noqa: E731
            uncached = sum(r["input_tokens"] - r["cached_tokens"] for r in rs) / n
            lines.append(f"| {arm} | {family} | {sum(r['success'] for r in rs)}/{n} | {mean('score'):.2f} | "
                         f"{mean('tokens'):,.0f} | {uncached:,.0f} | {mean('compactions'):.1f} | "
                         f"{mean('ctx_edits'):.1f} | {sum(r['searched'] for r in rs)}/{n} |")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(text + "\n")


def selftest():
    for family in FAMILIES + ["ledger"]:
        files, turns, expected = make(family, 0)
        assert make(family, 0) == (files, turns, expected), "not deterministic"
        chars = sum(len(t) for t in files.values())
        if family == "rules":
            order = sorted(expected.items(), key=lambda kv: -kv[1])
            good = "\n".join(["REPORT v7"] + [f"{k.upper()} | {s}" for k, s in order]
                             + [f"TOTAL {sum(expected.values())}", "-- end of report --"])
            bad = good.replace("REPORT v7\n", "")
            lowered = good.replace(order[0][0].upper(), order[0][0])
        else:
            good = "```json\n" + json.dumps(expected) + "\n```"
            wrong = dict(expected)
            wrong[next(iter(wrong))] = "WRONG"
            bad, lowered = json.dumps(wrong), json.dumps({**expected, "extra": "x"})
        assert grade(family, good, expected) == (1.0, True), family
        assert grade(family, bad, expected)[1] is False, family
        assert grade(family, lowered, expected)[1] is False, family
        assert grade(family, "no idea", expected) == (0.0, False), family
        if family != "kv":
            name, seen = next(iter(files)), []
            while name != "none":
                seen.append(name)
                text = files[name]
                assert family == "ledger" or sum(k in text for k in expected) == 1, name
                name = re.search(r"NEXT FILE: (\S+)", text).group(1)
            assert sorted(seen) == sorted(files), "chain does not visit every file once"
        print(f"{family}: {len(files)} files, ~{chars // 4:,} tokens, {len(turns)} turns, expected {expected}")
    print("selftest ok")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--out", default="/tmp/expb")
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--families", default=",".join(FAMILIES))
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--report")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.report:
        return report(a.report)
    os.makedirs(a.out, exist_ok=True)
    jobs = [(arm, fam, rep) for rep in range(a.repeats) for fam in a.families.split(",") for arm in a.arms.split(",")]
    with cf.ThreadPoolExecutor(a.workers) as pool:
        for f in cf.as_completed([pool.submit(one, a.out, *j) for j in jobs]):
            f.result()
    report(a.out)


if __name__ == "__main__":
    main()
