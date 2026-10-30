"""Experiment D: does the harness fix its habits better by changing its own
code than by writing lessons?

  python3 eval/experiment_d.py --selftest
  python3 eval/experiment_d.py --smoke /tmp/expd-smoke   # one code-arm episode
  python3 eval/experiment_d.py --repeats 3 --out /tmp/expd
  python3 eval/experiment_d.py --report /tmp/expd

Experiment C found that lessons about context management sounded right but
did not change what the agent did. Here two gated learning arms run C's CLM
episodes (task, review, then /refine), each chain in its own copy of the rig
home, so code one chain writes cannot reach another:

  lessons  /refine may record lessons only: self-modification is off
           (CONTINUAL_SELF_MODIFY=0) and the home's plugin code is read-only.
  code     /refine may record lessons or write, build and load a plugin.
A change, to lessons or code, is kept only if it builds, the gate suite of
Experiment C loses no clean run (confirmed by a rerun), and it uses at most
TOKEN_SLACK times the tokens; a rejected code change is rolled back. Gate
and test runs never change code: self-modification is off and the code is
read-only. Test, on C's fresh seeds of the three families and the held-out
`ledger`: summary and clm in a pristine home, and clm in each chain's home
with its final lessons (and code).
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experiment_c import (LEARN, REFINE, TEST, TOKEN_SLACK, admit, clean, episode, learn_seeds,  # noqa: E402
                          run_copy, suite, test_seeds)
from run import fresh, lessons, log, parallel, read_jsonl  # noqa: E402

BASE_HOME = os.environ.get("RIG_HOME", os.path.expanduser("~/.rig"))
CONTINUAL_SRC = os.environ.get("CONTINUAL_SRC", "/home/user/workspace/rig-continual")
RIG_SOURCE = os.environ.get("RIG_SOURCE", "/home/user/workspace/rig")
REFINE_CODE = REFINE + (", and whether a lesson or code will change it. You can record lessons, or change your own "
                        "code: a small rig plugin, such as a tool, a check or a guard, makes a behavior automatic or a "
                        "mistake impossible in every later session. Choose whichever will most reliably change what you "
                        "do next time. Keep a plugin small and general, useful on any task that reads files under a "
                        "token budget; build it, record it with harness_state (kind skill), and call harness_reload")
FROZEN = {"CONTINUAL_SELF_MODIFY": "0"}
ARMS = ["summary", "clm", "clm_lessons", "clm_code"]


# ------------------------------------------------------------ rig homes and their code

def make_home(path):
    """A copy of the base rig home with its own build, plugins and copy of `continual`."""
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    for name in ("bin", "plugins", "plugins.toml", "project", "target", "defaults.json", "data"):
        subprocess.run(["cp", "-a", os.path.join(BASE_HOME, name), path], check=True)
    dst = os.path.join(path, "plugins", "continual")
    shutil.copytree(CONTINUAL_SRC, dst, ignore=shutil.ignore_patterns("target"))
    toml = os.path.join(path, "plugins.toml")
    with open(toml) as f:
        text = f.read().replace(f'path = "{CONTINUAL_SRC}"', 'path = "plugins/continual"')
    with open(toml, "w") as f:
        f.write(text)
    assert build(path), f"{path} does not build"
    return path


BUILDS = threading.Semaphore(2)  # each cargo build peaks near 1.1 GB; the orb has 6 GiB


def build(home):
    """Whether the agent in `home` builds."""
    env = {**os.environ, "RIG_HOME": home}
    with BUILDS:
        return subprocess.run(["rig", "build"], env=env, capture_output=True).returncode == 0


def code_paths(home):
    """The agent's own code in `home`: plugins.toml and every plugin source file."""
    paths = ["plugins.toml"]
    for root, dirs, files in os.walk(os.path.join(home, "plugins")):
        dirs[:] = sorted(d for d in dirs if d != "target")
        paths += sorted(os.path.relpath(os.path.join(root, f), home) for f in files)
    return paths


def code_hash(home):
    h = hashlib.sha256()
    for p in code_paths(home):
        with open(os.path.join(home, p), "rb") as f:
            h.update(p.encode() + b"\0" + f.read() + b"\0")
    return h.hexdigest()


def set_writable(home, writable):
    """Lets the agent change its code in `home`, or makes the code read-only."""
    mode = "u+w" if writable else "a-w"
    subprocess.run(["chmod", "-R", mode, os.path.join(home, "plugins"), os.path.join(home, "plugins.toml")],
                   check=True)


def snapshot(home, dst):
    """Copies the code of `home` to `dst` (plugins.toml and plugins/, without builds)."""
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(os.path.join(home, "plugins"), os.path.join(dst, "plugins"),
                    ignore=shutil.ignore_patterns("target"))
    shutil.copy(os.path.join(home, "plugins.toml"), dst)


def restore(home, src):
    """Puts the code `snapshot` saved in `src` back in `home`, and rebuilds."""
    set_writable(home, True)
    shutil.rmtree(os.path.join(home, "plugins"))
    shutil.copytree(os.path.join(src, "plugins"), os.path.join(home, "plugins"))
    shutil.copy(os.path.join(src, "plugins.toml"), home)
    assert build(home), f"{home} does not build after a rollback"


def new_plugins(home, base):
    """The plugin crates in `home` that the base home does not have."""
    have = set(os.listdir(os.path.join(base, "plugins")))
    return sorted(d for d in os.listdir(os.path.join(home, "plugins")) if d not in have and d != "continual")


# ------------------------------------------------------------ learning chains

def chain(out, arm, rep):
    """One gated learning chain in its own home; returns (home, final state)."""
    home = os.path.join(out, "homes", f"{arm}-r{rep}")
    state = fresh(os.path.join(out, "states", f"{arm}-r{rep}.json"))
    frozen = {"home": home, "env": FROZEN}
    set_writable(home, False)
    before = suite(out, f"{arm}-r{rep}-start", state, **frozen)
    for i, (fam, seed) in enumerate(learn_seeds(rep)):
        tag = f"{arm}-r{rep}-{i}"
        cand = os.path.join(out, "states", f"{tag}-cand.json")
        shutil.copy(state, cand)
        saved = os.path.join(out, "code", f"{tag}-before")
        snapshot(home, saved)
        old = code_hash(home)
        if arm == "code":
            set_writable(home, True)
            row = episode(out, tag, fam, seed, cand, refine=REFINE_CODE, home=home)
            set_writable(home, False)
        else:
            row = episode(out, tag, fam, seed, cand, home=home, env=FROZEN)
        code_changed = code_hash(home) != old
        changed = code_changed or lessons(cand) != lessons(state)
        entry = {"arm": arm, "rep": rep, "family": fam, "seed": seed, "changed": changed,
                 "code_changed": code_changed, "plugins": new_plugins(home, BASE_HOME)}
        if changed:
            built = not code_changed or build(home)
            entry["built"] = built
            if built:
                cs = suite(out, tag, cand, **frozen)
                ok, lost, cs, ratio = admit(out, tag, cand, cs, before, **frozen)
                entry.update(accepted=ok, lost=lost, token_ratio=ratio,
                             suite={f: clean(r) for f, r in cs.items()},
                             suite_before={f: clean(r) for f, r in before.items()})
            else:
                ok = False
                entry["accepted"] = False
            if ok:
                shutil.copy(cand, state)
                before = cs
            elif code_changed:
                snapshot(home, os.path.join(out, "code", f"{tag}-rejected"))
                restore(home, saved)
                set_writable(home, False)
        entry.update(clean=clean(row), tokens=row["tokens"], lessons=len(lessons(state)),
                     plugins_kept=new_plugins(home, BASE_HOME))
        log(out, "chains.jsonl", entry)
        print(f"[{arm} r{rep}] {fam}/{seed}: clean={entry['clean']} changed={changed} code={code_changed} "
              f"accepted={entry.get('accepted')} ratio={entry.get('token_ratio')} plugins={entry['plugins_kept']}",
              flush=True)
    snapshot(home, os.path.join(out, "code", f"{arm}-r{rep}-final"))
    return home, state


def learn_and_test(out, arm, rep):
    """A learning chain, then the tests of what it learned."""
    home, state = chain(out, arm, rep)
    return parallel(tests(out, f"clm_{arm}", rep, state, home))


def tests(out, arm, rep, state, home, mode="clm"):
    return [lambda f=f, s=s: run_copy(out, arm, f, s, state, f"test-{arm}-r{rep}", "results.jsonl", mode,
                                      home=home, env=FROZEN)
            for f, s in test_seeds(rep)]


# ------------------------------------------------------------ report, selftest, main

def report(out):
    rows = read_jsonl(os.path.join(out, "results.jsonl"))
    lines = ["| arm | family | clean | correct | broke read-only | mean tokens | ctx edits | tool calls |",
             "|---|---|---|---|---|---|---|---|"]
    for family in TEST + ["learned (3)", "all"]:
        for arm in ARMS:
            members = {"all": TEST, "learned (3)": LEARN}.get(family, [family])
            rs = [r for r in rows if r["arm"] == arm and r["family"] in members]
            if not rs:
                continue
            n = len(rs)
            lines.append(f"| {arm} | {family} | {sum(map(clean, rs))}/{n} | {sum(r['success'] for r in rs)}/{n} | "
                         f"{sum(r['searched'] for r in rs)}/{n} | {sum(r['tokens'] for r in rs) / n:,.0f} | "
                         f"{sum(r['ctx_edits'] for r in rs) / n:.1f} | {sum(r['tool_calls'] for r in rs) / n:.1f} |")
    path = os.path.join(out, "chains.jsonl")
    ch = read_jsonl(path) if os.path.exists(path) else []
    for arm in ("lessons", "code"):
        g = [c for c in ch if c["arm"] == arm and c["changed"]]
        code = [c for c in g if c["code_changed"]]
        lines += ["", f"{arm}: {sum(c['accepted'] for c in g)}/{len(g)} changes accepted; code changes "
                  f"{len(code)} ({sum(c['built'] for c in code)} built, {sum(c['accepted'] for c in code)} accepted); "
                  f"rejected for lost clean runs {sum(bool(c.get('lost')) for c in g)}, "
                  f"for tokens over {TOKEN_SLACK}x {sum((c.get('token_ratio') or 0) > TOKEN_SLACK for c in g)}."]
        for rep in sorted({c["rep"] for c in ch if c["arm"] == arm}):
            last = [c for c in ch if c["arm"] == arm and c["rep"] == rep][-1]
            st = os.path.join(out, "states", f"{arm}-r{rep}.json")
            lines.append(f"- r{rep}: plugins kept {last['plugins_kept'] or 'none'}, "
                         f"{len(lessons(st)) if os.path.exists(st) else '?'} lessons")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(text + "\n")


def selftest():
    import tempfile
    home = tempfile.mkdtemp()
    os.makedirs(os.path.join(home, "plugins", "a", "src"))
    os.makedirs(os.path.join(home, "plugins", "a", "target"))
    for p, t in {"plugins.toml": "x", "plugins/a/src/lib.rs": "fn a() {}", "plugins/a/target/junk": "1"}.items():
        with open(os.path.join(home, p), "w") as f:
            f.write(t)
    h = code_hash(home)
    with open(os.path.join(home, "plugins/a/target/junk"), "w") as f:
        f.write("2")
    assert code_hash(home) == h, "build output counted as code"
    set_writable(home, False)
    try:
        open(os.path.join(home, "plugins/a/src/new.rs"), "w")
        raise AssertionError("read-only code was writable")
    except PermissionError:
        pass
    set_writable(home, True)
    with open(os.path.join(home, "plugins/a/src/lib.rs"), "w") as f:
        f.write("fn b() {}")
    assert code_hash(home) != h, "code change not seen"
    assert "harness_reload" in REFINE_CODE and REFINE_CODE.startswith(REFINE)
    shutil.rmtree(home)
    print("selftest ok")


def smoke(out):
    """One code-arm learning episode in a new home: does the agent write a plugin?"""
    home = make_home(os.path.join(out, "homes", "smoke"))
    state = fresh(os.path.join(out, "states", "smoke.json"))
    old = code_hash(home)
    row = episode(out, "smoke", "kv", 100, state, refine=REFINE_CODE, home=home)
    print(f"clean={clean(row)} code_changed={code_hash(home) != old} plugins={new_plugins(home, BASE_HOME)} "
          f"builds={build(home)} lessons={[l['text'][:100] for l in lessons(state)]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--out", default="/tmp/expd")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--smoke")
    p.add_argument("--report")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.report:
        return report(a.report)
    if a.smoke:
        return smoke(a.smoke)
    os.makedirs(os.path.join(a.out, "states"), exist_ok=True)
    # Agents must not change rig itself, which every home builds from.
    subprocess.run(["chmod", "-R", "a-w", RIG_SOURCE], check=True)
    try:
        base = make_home(os.path.join(a.out, "homes", "base"))
        set_writable(base, False)
        for arm in ("lessons", "code"):
            for r in range(a.repeats):
                make_home(os.path.join(a.out, "homes", f"{arm}-r{r}"))
        empty = fresh(os.path.join(a.out, "states", "empty.json"))
        reps = range(a.repeats)
        baseline = [t for r in reps for t in tests(a.out, "summary", r, empty, base, "summary")
                    + tests(a.out, "clm", r, empty, base)]
        learned = [lambda arm=arm, r=r: learn_and_test(a.out, arm, r) for arm in ("lessons", "code") for r in reps]
        # Baseline tests run beside the chains; each chain's tests follow it.
        parallel(baseline + learned)
    finally:
        subprocess.run(["chmod", "-R", "u+w", RIG_SOURCE], check=True)
    report(a.out)


if __name__ == "__main__":
    main()
