"""Level 2 eval: can the agent give itself a working tool by writing a plugin?

  RIG_HOME=/some/copy python3 eval/self_modify.py --out /tmp/selfmod-out

Each task asks the agent for a new tool. Then a fresh session must call that
tool, and its output is compared with a reference command. `repair` instead
starts from a plugin that does not compile. Tasks run one at a time, each
from the same plugins.toml, because they rebuild the shared agent.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from run import fresh, lessons, log, rig, write_files  # noqa: E402

HOME = os.environ["RIG_HOME"]
DATA = "alpha\nbeta TODO: fix me\ngamma\n# TODO second\n"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


TASKS = {
    "sha256": dict(
        ask="Give yourself a tool named sha256_file that takes a `path` argument and returns the lowercase hex SHA-256 of that file's bytes. Write it as a new rig plugin named sha_tool (no new dependencies: implement SHA-256 yourself or find a crate already in the rig workspace's Cargo.lock that you can add), make it build, record it with harness_state, then call harness_reload.",
        use="Call the sha256_file tool on data.txt and reply with exactly its output, nothing else.",
        tool="sha256_file",
        expect=lambda d: sha(os.path.join(d, "data.txt")),
    ),
    "count_lines": dict(
        ask="Give yourself a tool named count_lines that takes a `path` argument and returns the number of lines in that file as plain digits. Write it as a new rig plugin named line_tool, make it build, record it with harness_state, then call harness_reload.",
        use="Call the count_lines tool on data.txt and reply with exactly its output, nothing else.",
        tool="count_lines",
        expect=lambda d: str(DATA.count("\n")),
    ),
    "todos": dict(
        ask="Give yourself a tool named list_todos that takes a `path` argument and returns each line containing `TODO` as `<line number>: <line>` (1-based), one per line. Write it as a new rig plugin named todo_tool, make it build, record it with harness_state, then call harness_reload.",
        use="Call the list_todos tool on data.txt and reply with exactly its output, nothing else.",
        tool="list_todos",
        expect=lambda d: "2: beta TODO: fix me\n4: # TODO second",
    ),
    "repair": dict(
        ask="The agent plugin at $RIG_HOME/plugins/echo_tool does not compile, so `rig plugin check --build` fails. Fix the plugin's code so the agent builds, then call harness_reload.",
        use="Call the echo_upper tool with text `hello rig` and reply with exactly its output, nothing else.",
        tool="echo_upper",
        expect=lambda d: "HELLO RIG",
        broken=True,
    ),
}

BROKEN = '''use rig_harness::prelude::*;
use schemars::JsonSchema;
use serde::Deserialize;

#[derive(Default)]
pub struct EchoToolPlugin;

#[derive(Deserialize, JsonSchema)]
struct Args {
    /// The text.
    text: String,
}

impl Plugin for EchoToolPlugin {
    fn build(&self, app: &mut App) {
        app.add_open_tool("echo_upper", "Returns the text in upper case.", ToolOptions::default(), echo);
    }
}

fn echo(called: On<ToolCalled<Args>>, mut calls: Query<&mut Decision, ToolCalls>) {
    if let Ok(mut decision) = calls.get_mut(called.call) {
        let text = called.args.text.to_upper();
        decision.decide(Decider::plugin("echo"), ToolResult::success(text.into()))
    }
}
'''


def plant_broken():
    """A plugin crate made by `rig plugin new`, with two compile errors."""
    env = {**os.environ, "RIG_HOME": HOME}
    subprocess.run(["rig", "plugin", "new", "echo_tool"], env=env, check=True, capture_output=True)
    with open(os.path.join(HOME, "plugins/echo_tool/src/lib.rs"), "w") as f:
        f.write(BROKEN)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="/tmp/selfmod-out")
    p.add_argument("--tasks", default=",".join(TASKS))
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    toml = os.path.join(HOME, "plugins.toml")
    with open(toml) as f:
        pristine = f.read()
    for name in a.tasks.split(","):
        t = TASKS[name]
        write_files(HOME, {"plugins.toml": pristine})
        for d in os.listdir(os.path.join(HOME, "plugins")):
            if d not in ("continual", "utc_clock"):
                shutil.rmtree(os.path.join(HOME, "plugins", d))
        repo = os.path.join(a.out, name)
        shutil.rmtree(repo, ignore_errors=True)
        write_files(repo, {"data.txt": DATA})
        state = fresh(os.path.join(repo, "state.json"))
        if t.get("broken"):
            plant_broken()
        build = rig(repo, t["ask"].replace("$RIG_HOME", HOME), state, timeout=1500)
        use = rig(repo, t["use"], state, timeout=300)
        called = any(c["name"] == t["tool"] for c in use["tool_calls"])
        got = use["answer"].strip().strip("`").strip()
        want = t["expect"](repo)
        reloads = sum(1 for c in build["tool_calls"] if c["name"] == "harness_reload")
        r = {"task": name, "build_code": build["code"], "reload_calls": reloads, "lesson_recorded": bool(lessons(state)),
             "tool_called_in_new_session": called, "output_correct": called and got == want,
             "build_tool_calls": len(build["tool_calls"]), "build_seconds": build["seconds"],
             "cost": round(build["cost"] + use["cost"], 4), "got": got[:200], "want": want}
        log(a.out, "results.jsonl", r)
        print(json.dumps(r), flush=True)
    write_files(HOME, {"plugins.toml": pristine})


if __name__ == "__main__":
    main()
