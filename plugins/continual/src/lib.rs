//! `continual`: a continual-learning, self-modifying harness for the rig agent.
//!
//! Level 1, learned state: lessons (prompt notes, memories, skill
//! descriptions, subagent specs) kept across sessions in
//! `RIG_HOME/data/continual/state.json`, shown to every agent as a system
//! prompt section, edited by the model through the `harness_state` tool with
//! a versioned history it can roll back. `/refine` asks the agent to review
//! its own work and record small, evidence-backed lessons.
//!
//! Level 2, learned code: the agent writes new rig plugins (Rust crates),
//! lists them in `plugins.toml`, and calls `harness_reload`, which rebuilds
//! the agent and restarts it in the same session. A build that fails keeps
//! the running build and puts the compiler errors in the conversation; a
//! build that crashes at startup is rolled back by the launcher.

use rig_basics::reload::ReloadStatus;
use rig_harness::prelude::*;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

const NAME: &str = "continual";
const STATE_FILE: &str = "continual/state.json";
/// Overrides where the state is kept: a file path, such as one per eval run.
const STATE_ENV: &str = "CONTINUAL_STATE";
/// How many earlier versions the history keeps.
const HISTORY: usize = 50;

#[derive(Default)]
pub struct ContinualPlugin;

impl Plugin for ContinualPlugin {
    fn build(&self, app: &mut App) {
        let writes = ToolOptions { footprint: Some(Touches::Writes(Scope::Anything)) };
        app.init_resource::<Harness>()
            .add_open_tool("harness_state", STATE_TOOL, writes.clone(), on_state)
            .add_open_tool("harness_reload", RELOAD_TOOL, writes, on_reload)
            .add_command("refine", "Review this session and record lessons in the harness", refine)
            .add_command("harness", "Show the learned harness state; `/harness rollback <version>`", show)
            .add_systems(Update, (load, sync_section).chain().before(AgentLoop));
    }
}

/// One learned lesson.
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
struct Lesson {
    id: u32,
    kind: Kind,
    text: String,
    /// What in a trajectory showed it was needed.
    evidence: String,
}

#[derive(Clone, Copy, Serialize, Deserialize, JsonSchema, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum Kind {
    /// An instruction added to the system prompt.
    Prompt,
    /// A fact about the user, project or environment.
    Memory,
    /// A reusable procedure: when to use it and its steps.
    Skill,
    /// A reusable subagent: its role and instructions.
    Subagent,
}

/// A recorded version of the lessons, for rollback.
#[derive(Clone, Serialize, Deserialize, Debug)]
struct Version {
    version: u32,
    reason: String,
    lessons: Vec<Lesson>,
}

#[derive(Clone, Serialize, Deserialize, Debug, Default)]
struct State {
    version: u32,
    next_id: u32,
    lessons: Vec<Lesson>,
    history: Vec<Version>,
}

impl State {
    /// Records the current lessons in the history, then applies `change`.
    fn commit(&mut self, reason: &str, change: impl FnOnce(&mut Self)) {
        self.history.push(Version {
            version: self.version,
            reason: reason.to_owned(),
            lessons: self.lessons.clone(),
        });
        if self.history.len() > HISTORY {
            self.history.remove(0);
        }
        change(self);
        self.version += 1;
    }

    fn rollback(&mut self, version: u32) -> Result<(), String> {
        if version == self.version {
            return Err(format!("Version {version} is the current one."));
        }
        let Some(old) = self.history.iter().find(|v| v.version == version).cloned() else {
            let kept: Vec<String> = self.history.iter().map(|v| v.version.to_string()).collect();
            return Err(format!("No version {version}; kept: {}", kept.join(", ")));
        };
        self.commit(&format!("rollback to {version}"), |s| s.lessons = old.lessons);
        Ok(())
    }

    fn listing(&self) -> String {
        if self.lessons.is_empty() {
            return format!("Harness version {}: no lessons yet.", self.version);
        }
        let mut out = format!("Harness version {}:\n", self.version);
        for l in &self.lessons {
            out.push_str(&format!("- #{} [{:?}] {} (evidence: {})\n", l.id, l.kind, l.text, l.evidence));
        }
        out
    }

    fn history_listing(&self) -> String {
        let mut out = format!("Current version {}. Earlier versions:\n", self.version);
        for v in self.history.iter().rev() {
            out.push_str(&format!("- v{} ({} lessons), left by: {}\n", v.version, v.lessons.len(), v.reason));
        }
        out
    }

    /// The system prompt text of the lessons.
    fn section(&self) -> String {
        let mut out = String::from(
            "Lessons this harness learned from earlier work. Follow them unless the user says otherwise; \
             change them with `harness_state`.\n",
        );
        for kind in [Kind::Prompt, Kind::Memory, Kind::Skill, Kind::Subagent] {
            let of_kind: Vec<&Lesson> = self.lessons.iter().filter(|l| l.kind == kind).collect();
            if of_kind.is_empty() {
                continue;
            }
            out.push_str(&format!("\n{kind:?}:\n"));
            for l in of_kind {
                out.push_str(&format!("- #{}: {}\n", l.id, l.text));
            }
        }
        out
    }
}

/// The learned state, once read from disk.
#[derive(Resource, Default)]
struct Harness {
    state: State,
    loaded: bool,
}

impl Harness {
    /// Writes the state to `CONTINUAL_STATE`, or else to the user's plugin data.
    fn save(&self, data: Option<&PluginData>) -> Result<(), String> {
        let data = data.ok_or("plugin data is not ready yet")?;
        let bytes = serde_json::to_vec_pretty(&self.state).map_err(|e| e.to_string())?;
        match std::env::var_os(STATE_ENV) {
            Some(path) => std::fs::write(path, bytes).map_err(|e| e.to_string()),
            None => data.user.write_file(STATE_FILE, &bytes).map_err(|e| e.to_string()),
        }
    }
}

/// Reads the state once `PluginData` exists.
fn load(mut harness: ResMut<Harness>, data: Option<Res<PluginData>>, mut notices: MessageWriter<Notice>) {
    let Some(data) = data else { return };
    if harness.loaded {
        return;
    }
    harness.loaded = true;
    let read = match std::env::var_os(STATE_ENV) {
        Some(path) => std::fs::read(path),
        None => data.user.read_file(STATE_FILE),
    };
    match read {
        Ok(bytes) => match serde_json::from_slice(&bytes) {
            Ok(state) => harness.state = state,
            Err(e) => {
                notices.write(Notice::warn(None, format!("continual: {STATE_FILE} unreadable ({e}); starting empty")));
            }
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => {
            notices.write(Notice::warn(None, format!("continual: reading {STATE_FILE}: {e}")));
        }
    }
}

/// On the section's entity.
#[derive(Component)]
struct LessonsSection;

/// Keeps the prompt section in step with the lessons. A changed section
/// costs the prompt cache once, so lessons change only through small edits.
fn sync_section(
    harness: Res<Harness>,
    home: Option<Res<RigHome>>,
    mut sections: Query<&mut PromptSection, With<LessonsSection>>,
    mut commands: Commands,
) {
    if !harness.is_changed() || !harness.loaded {
        return;
    }
    let home = home.map(|h| h.0.root().display().to_string()).unwrap_or_else(|| "$RIG_HOME".into());
    let text = format!("{}\n{}", harness.state.section(), self_modify_guide(&home));
    match sections.single_mut() {
        Ok(mut section) => {
            if section.text != text {
                section.text = text;
            }
        }
        Err(_) => {
            commands.spawn((
                Name::new("prompt:continual"),
                LessonsSection,
                PromptSection::new(PromptSection::ORDER_PROJECT + 50, "continual_harness", text),
            ));
        }
    }
}

fn self_modify_guide(home: &str) -> String {
    format!(
        "You can also change your own code. You are a rig agent: a Bevy app built from the Rust plugins listed in \
         {home}/plugins.toml. To add a capability that lessons cannot give (a new tool, gate, command or hook):\n\
         1. Run `RIG_HOME={home} rig plugin new <name>` with the shell tool; it makes {home}/plugins/<name> and lists it.\n\
         2. Edit its src/lib.rs. The plugin guide is the rig checkout's crates/rig-harness/PLUGINS.md (RIG_SOURCE).\n\
         3. Run `RIG_HOME={home} rig plugin check --build` until it compiles.\n\
         4. Call `harness_reload` to restart on the new build in this session.\n\
         Record each such change with `harness_state` (kind `skill`) so later sessions know it exists."
    )
}

#[derive(Deserialize, JsonSchema, Clone, Copy, Debug)]
#[serde(rename_all = "lowercase")]
enum Action {
    List,
    History,
    Add,
    Update,
    Remove,
    Rollback,
}

/// The arguments of `harness_state`.
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct StateArgs {
    /// `list` the lessons, show the `history`, `add` one, `update` or `remove` one by `id`, or `rollback` to a `version`.
    action: Action,
    /// For `add`: the kind of lesson.
    kind: Option<Kind>,
    /// For `add` and `update`: the lesson, one short imperative or fact.
    text: Option<String>,
    /// For `add`, `update` and `remove`: what in this session showed it is needed. Required for changes.
    evidence: Option<String>,
    /// For `update` and `remove`: the lesson's id.
    id: Option<u32>,
    /// For `rollback`: the version to return to.
    version: Option<u32>,
}

const STATE_TOOL: &str = "Read and change the harness's learned state: lessons kept across sessions and shown in \
your system prompt. Kinds: `prompt` (an instruction), `memory` (a fact), `skill` (a procedure), `subagent` (a role). \
Change it only with small edits backed by evidence from real work, such as a mistake you made and fixed, never \
speculative ones. Every change is a new version; `rollback` undoes.";

const RELOAD_TOOL: &str = "Rebuild this agent from plugins.toml and its plugin crates, and restart it in this \
session once no turn runs. Call it after changing a plugin's code and checking that it builds. A failed build \
keeps the running one and its errors come back to you.";

fn on_state(
    called: On<ToolCalled<StateArgs>>,
    mut harness: ResMut<Harness>,
    data: Option<Res<PluginData>>,
    mut calls: Query<&mut Decision, ToolCalls>,
) {
    let result = apply(&mut harness, &called.args).and_then(|(text, changed)| {
        if changed {
            harness.save(data.as_deref())?;
        }
        Ok(text)
    });
    answer(&mut calls, called.call, result);
}

/// Answers tool call `call` with `result`'s text, or refuses it with its error.
fn answer(calls: &mut Query<&mut Decision, ToolCalls>, call: Entity, result: Result<String, String>) {
    if let Ok(mut decision) = calls.get_mut(call) {
        let outcome = match result {
            Ok(text) => ToolResult::success(text.into()),
            Err(why) => ToolResult::failed(ToolExecutionError::refused(why)),
        };
        decision.decide(Decider::plugin(NAME), outcome);
    }
}

/// `value` when it is there and not blank, else the error `missing`.
fn required(value: &Option<String>, missing: &str) -> Result<String, String> {
    value.clone().filter(|v| !v.trim().is_empty()).ok_or_else(|| missing.to_owned())
}

/// Applies a `harness_state` call; the text answers it, the flag says whether the state changed.
fn apply(harness: &mut Harness, args: &StateArgs) -> Result<(String, bool), String> {
    let state = &mut harness.state;
    let evidence = || required(&args.evidence, "A change needs `evidence`: what in this session showed it is needed.");
    let position = |state: &State| {
        let id = args.id.ok_or("This action needs `id`.")?;
        state.lessons.iter().position(|l| l.id == id).ok_or(format!("No lesson #{id}."))
    };
    match args.action {
        Action::List => Ok((state.listing(), false)),
        Action::History => Ok((state.history_listing(), false)),
        Action::Add => {
            let kind = args.kind.ok_or("`add` needs `kind`.")?;
            let text = required(&args.text, "`add` needs `text`.")?;
            let evidence = evidence()?;
            let id = state.next_id;
            state.commit(&format!("add #{id}"), |s| {
                s.next_id += 1;
                s.lessons.push(Lesson { id, kind, text, evidence });
            });
            Ok((format!("Added lesson #{id}; harness is now version {}.", state.version), true))
        }
        Action::Update => {
            let at = position(state)?;
            let text = required(&args.text, "`update` needs `text`.")?;
            let evidence = evidence()?;
            let id = state.lessons[at].id;
            state.commit(&format!("update #{id}"), |s| {
                s.lessons[at].text = text;
                s.lessons[at].evidence = evidence;
            });
            Ok((format!("Updated lesson #{id}; harness is now version {}.", state.version), true))
        }
        Action::Remove => {
            let at = position(state)?;
            let evidence = evidence()?;
            let id = state.lessons[at].id;
            state.commit(&format!("remove #{id}: {evidence}"), |s| {
                s.lessons.remove(at);
            });
            Ok((format!("Removed lesson #{id}; harness is now version {}.", state.version), true))
        }
        Action::Rollback => {
            let version = args.version.ok_or("`rollback` needs `version`.")?;
            state.rollback(version)?;
            Ok((format!("Rolled back to the lessons of version {version}; harness is now version {}.", state.version), true))
        }
    }
}

/// The arguments of `harness_reload`.
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ReloadArgs {
    /// What changed, for the record.
    reason: String,
}

fn on_reload(
    called: On<ToolCalled<ReloadArgs>>,
    reload: Option<ResMut<ReloadStatus>>,
    mut calls: Query<&mut Decision, ToolCalls>,
) {
    let result = match reload {
        Some(mut reload) => reload.ask(called.agent).map_err(str::to_owned),
        None => Err("The ReloadPlugin is not loaded, so this agent cannot rebuild itself.".to_owned()),
    };
    let result = result.map(|()| {
        format!(
            "Reload queued ({}): the build starts once this turn ends, and the agent restarts on it in this session.",
            called.args.reason
        )
    });
    answer(&mut calls, called.call, result);
}

const REFINE: &str = "Review this session's work so far. Find at most three concrete lessons that would have made it \
go better: a mistake you made and how you fixed it, a fact about this project or user you had to discover, or a \
procedure worth reusing. First call `harness_state` with `list` to see what is already known. Then record each new \
lesson with `add`, fix a wrong one with `update`, or drop one that misled you with `remove`, always with `evidence` \
from this session. Do not record anything speculative or already known. If nothing qualifies, say so. If a lesson \
needs code (a new tool, gate or check) rather than words, say what plugin would provide it.";

/// `/refine [focus]`: asks the agent to review its work and record lessons.
fn refine(In(args): In<CommandArgs>, mut commands: Commands) {
    let mut text = REFINE.to_owned();
    if !args.args.trim().is_empty() {
        text.push_str(&format!("\n\nFocus on: {}", args.args.trim()));
    }
    let deliver = Deliver::new(args.agent, text, DeliveryMode::Queue);
    commands.trigger(deliver.with_author(Author::plugin(NAME)));
}

/// `/harness`, `/harness history`, `/harness rollback <version>`.
fn show(
    In(args): In<CommandArgs>,
    mut harness: ResMut<Harness>,
    data: Option<Res<PluginData>>,
    mut notices: MessageWriter<Notice>,
) {
    let words: Vec<&str> = args.args.split_whitespace().collect();
    let notice = match words.as_slice() {
        [] => Notice::info(args.agent, harness.state.listing()),
        ["history"] => Notice::info(args.agent, harness.state.history_listing()),
        ["rollback", version] => {
            let rolled = version
                .parse::<u32>()
                .map_err(|_| "/harness rollback needs a version number".to_owned())
                .and_then(|v| harness.state.rollback(v).and_then(|()| harness.save(data.as_deref())).map(|()| v));
            match rolled {
                Ok(v) => Notice::info(args.agent, format!("Rolled back to version {v}.")),
                Err(why) => Notice::error(args.agent, why),
            }
        }
        _ => Notice::error(args.agent, "Usage: /harness, /harness history, /harness rollback <version>"),
    };
    notices.write(notice);
}
