# rig-harness: a continual-learning, self-modifying coding agent on Rig

A terminal coding agent built on [Rig](https://github.com/0xPlaygrounds/rig)'s
plugin harness (a Bevy ECS app where everything is a plugin), extended so it
learns across sessions and can rewrite its own code. Tested with
`openrouter/z-ai/glm-5.3-flash`.

## How it learns

**Level 1: learned state** (`plugins/continual`)
- `harness_state` tool: lessons of four kinds (`prompt`, `memory`, `skill`,
  `subagent`), kept across sessions in `$RIG_HOME/data/continual/state.json`
  and injected into every agent's system prompt. Every change needs
  `evidence` and creates a new version; any version can be restored.
- `/refine [focus]`: the agent reviews its session and records at most three
  evidence-backed lessons.
- `/harness`, `/harness history`, `/harness rollback <version>`.

**Level 2: learned code**
- The system prompt teaches the agent to write a new rig plugin
  (`rig plugin new`), build it (`rig plugin check --build`), record it as a
  skill, and call `harness_reload`, which rebuilds the agent and restarts it
  in the same session. A failed build keeps the running agent and feeds the
  compiler errors back; a crash at startup rolls back.
- `plugins/utc_clock` was written entirely by the agent (GLM-5.3-Flash) this
  way, when asked to give itself a `utc_now` tool. `examples/state.json` is
  the learned state from that run.

## Setup

```bash
./setup.sh                       # rig at the tested commit, launcher, plugins, first build
export OPENROUTER_API_KEY=...
source rig-env.sh
rig -m openrouter/z-ai/glm-5.3-flash
```

`RIG_HOME` defaults to `~/.rig` and `RIG_SOURCE` (the rig checkout) to `~/rig`.
`plugins.toml` is rig's default list plus `EffectLogPlugin`, `continual` and
`utc_clock`.

## Evals (GLM-5.3-Flash, 3 repeats)

**Level 1, continual learning** (`eval/run.py`): 8 task families, each with a
hidden team rule. Baseline runs each test task with empty memory; continual
first runs learning episodes in other repos (task, reviewer feedback stating
the rule, `/refine`), then the test task in a new repo that never mentions it.

| | Baseline | Continual |
|---|---|---|
| Tasks solved | 24/24 | 24/24 |
| Rule followed | 3/21 | 20/21 |
| `APP_ENV` used on first test run | 0/3 | 3/3 |
| Avg tool calls | 6.1 | 9.1 |

**Level 2, self-modification** (`eval/self_modify.py`): the agent wrote,
built and reloaded a plugin for `sha256_file`, `count_lines` and
`list_todos`, and repaired a planted broken plugin; a fresh session's tool
output was correct in 4/4.

Raw results are in `eval/results/`. `eval/selftest.py` checks that every
grader fails the untouched repo and passes a reference fix. Set
`CONTINUAL_STATE=<file>` to give a run its own learned state.

## Caveats

- Plugin code is fully trusted and the agent can edit `plugins.toml`: run it
  in a sandbox. There is no approval gate on `harness_reload` yet.
- Lessons live in the system prompt, so each change costs one prompt-cache miss.
- This improves the harness, not the model's weights.
