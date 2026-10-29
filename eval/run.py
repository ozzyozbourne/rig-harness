"""Continual-learning eval for the rig `continual` harness.

  python3 eval/run.py --repeats 3 --out /tmp/eval-out

Conditions, per repeat:
  baseline   each test task with an empty harness state.
  continual  all learning episodes run in one shared state (task, reviewer
             feedback, /refine), then each test task with a copy of the
             state that produced.
Needs `rig` on PATH with the continual plugin, and OPENROUTER_API_KEY.
"""

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from tasks import FAMILIES  # noqa: E402

MODEL = "openrouter/z-ai/glm-5.3-flash"
EMPTY = {"version": 0, "next_id": 0, "lessons": [], "history": []}


def make_repo(path, files):
    shutil.rmtree(path, ignore_errors=True)
    for name, text in files.items():
        full = os.path.join(path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
    subprocess.run("git init -q && git add -A && git -c user.name=eval -c user.email=eval@x commit -qm init",
                   shell=True, cwd=path, check=True)


def rig(repo, prompt, state, session=None, timeout=420):
    """One `rig -p --json` run; returns a summary of its events."""
    cmd = ["rig", "-m", MODEL, "-p", "--json"]
    if session:
        cmd += ["-r", session]
    cmd.append(prompt)
    env = {**os.environ, "CONTINUAL_STATE": state}
    start = time.time()
    try:
        out = subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    exit_ = next((e for e in events if e.get("type") == "exit"), {})
    calls = [e for e in events if e.get("type") == "tool_call"]
    usage = exit_.get("usage") or {}
    return {
        "code": exit_.get("code"),
        "session": exit_.get("session") or next((e.get("session") for e in events if e.get("type") == "ready"), None),
        "answer": exit_.get("answer") or "",
        "error": exit_.get("error"),
        "tool_calls": [{"name": c.get("name"), "args": c.get("args")} for c in calls],
        "cost": usage.get("cost", 0.0),
        "tokens": (usage.get("tokens") or {}).get("total_tokens", 0),
        "seconds": round(time.time() - start, 1),
    }


def lessons(state):
    with open(state) as f:
        return json.load(f)["lessons"]


def test_run(out, cond, rep, fam, state_src):
    spec = FAMILIES[fam]
    repo = os.path.join(out, "runs", cond, f"r{rep}", fam)
    make_repo(repo, spec["test_files"])
    state = repo + ".state.json"
    shutil.copy(state_src, state)
    r = rig(repo, spec["test_task"], state)
    ok_task, ok_conv = spec["check"](repo, r["answer"])
    shell = [c["args"].get("command", "") for c in r["tool_calls"] if c["name"] == "shell" and isinstance(c["args"], dict)]
    test_cmds = [c for c in shell if "unittest" in c or "pytest" in c]
    r.update(cond=cond, rep=rep, family=fam, task_ok=ok_task, convention_ok=ok_conv,
             first_test_has_app_env=bool(test_cmds) and "APP_ENV" in test_cmds[0],
             failed_test_runs_before_app_env=next((i for i, c in enumerate(test_cmds) if "APP_ENV" in c), len(test_cmds)))
    with open(os.path.join(out, "results.jsonl"), "a") as f:
        f.write(json.dumps({k: v for k, v in r.items() if k != "tool_calls"} | {"n_tool_calls": len(r["tool_calls"])}) + "\n")
    print(f"[{cond} r{rep}] {fam}: task={ok_task} conv={ok_conv} calls={len(r['tool_calls'])} ${r['cost']:.4f}", flush=True)
    return r


def learn_chain(out, rep, families):
    """All learning episodes in one shared state; returns the state path."""
    state = os.path.join(out, "states", f"r{rep}.json")
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "w") as f:
        json.dump(EMPTY, f)
    log = []
    for fam in families:
        spec = FAMILIES[fam]
        repo = os.path.join(out, "runs", "learn", f"r{rep}", fam)
        make_repo(repo, spec["learn_files"])
        before = len(lessons(state))
        steps = []
        r = rig(repo, spec["learn_task"], state)
        steps.append(r)
        if r["session"]:
            steps.append(rig(repo, spec["feedback"], state, r["session"]))
            steps.append(rig(repo, "/refine", state, r["session"]))
        entry = {"rep": rep, "family": fam, "lessons_before": before, "lessons_after": len(lessons(state)),
                 "cost": sum(s["cost"] for s in steps), "codes": [s["code"] for s in steps],
                 "harness_calls": sum(1 for s in steps for c in s["tool_calls"] if c["name"] == "harness_state"
                                      and isinstance(c["args"], dict) and c["args"].get("action") != "list")}
        log.append(entry)
        with open(os.path.join(out, "learn.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"[learn r{rep}] {fam}: lessons {before}->{entry['lessons_after']} ${entry['cost']:.4f}", flush=True)
    return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--out", default="/tmp/eval-out")
    p.add_argument("--families", default=",".join(FAMILIES))
    p.add_argument("--workers", type=int, default=6)
    a = p.parse_args()
    families = a.families.split(",")
    os.makedirs(a.out, exist_ok=True)
    empty = os.path.join(a.out, "empty.json")
    with open(empty, "w") as f:
        json.dump(EMPTY, f)

    with cf.ThreadPoolExecutor(a.workers) as pool:
        # Learning chains run side by side with the baseline test runs.
        chains = {rep: pool.submit(learn_chain, a.out, rep, families) for rep in range(a.repeats)}
        jobs = [pool.submit(test_run, a.out, "baseline", rep, fam, empty)
                for rep in range(a.repeats) for fam in families]
        for rep, chain in chains.items():
            state = chain.result()
            jobs += [pool.submit(test_run, a.out, "continual", rep, fam, state) for fam in families]
        for j in jobs:
            j.result()


if __name__ == "__main__":
    main()
