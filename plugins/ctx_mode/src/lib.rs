//! `ctx_mode`: how an agent's context is kept under a token budget, chosen
//! per run by the `CTX_MODE` environment variable, for Experiment B.
//!
//! - unset or `summary`: nothing here; rig's default compaction runs at the
//!   budget its `[compaction]` reserve sets.
//! - `keep_last`: when a turn's request reaches `CTX_BUDGET` tokens, all
//!   but the task and the newest half budget of messages is taken out, with
//!   no summary.
//! - `clm`: the agent manages its own context (Context Language Models,
//!   arXiv 2609.37725). Its conversation is mirrored to a file,
//!   `.clm/<agent id>.md` in the project, one `[[CTX <id> <role>]]` block per
//!   message. The agent edits that file with its own tools; before each of
//!   its model calls, the edits become the conversation: a deleted block is
//!   removed, a rewritten block replaces its message, a block without an id
//!   is added as a note. Messages made since the file was last written are
//!   always kept, so the call that made an edit survives it. Near the
//!   budget the request carries a reminder. The default compaction stays as
//!   the fallback for an agent that lets its context overflow.
//!
//! Tokens are measured as rig's compaction measures them: the provider's
//! reported usage of the last reply plus an estimate of the messages after
//! it, or the whole request's estimate when that no longer holds. Every edit
//! is a recorded change by `Author::plugin("ctx_mode")`, so the session log
//! keeps what was removed, and each edit or cut also leaves a `ctx_mode.edit`
//! or `ctx_mode.cut` aside, which an eval can count.

use std::collections::HashMap;
use std::path::PathBuf;

use rig_harness::prelude::*;
use rig_harness::rig_core::completion::message::{Message as Msg, ToolCall, ToolResultContent, UserContent};
use rig_harness::rig_core::conversation::Aside;
use rig_harness::rig_memory::{Prepared, RequestStart, SummaryLimits, estimate_tokens, next_request_tokens};

const NAME: &str = "ctx_mode";
/// Tokens before the budget at which a `clm` request carries a reminder.
const REMIND_BEFORE: u64 = 4096;
/// The default compaction's reserve: the room it keeps free below the window.
const RESERVE: u64 = SummaryLimits::DEFAULT.reserve;
/// How far past the budget the default compaction acts as a fallback.
const FALLBACK_MARGIN: u64 = 8_192;

#[derive(Default)]
pub struct CtxModePlugin;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Summary,
    KeepLast,
    Clm,
}

#[derive(Resource)]
struct Config {
    mode: Mode,
    budget: u64,
}

/// A `clm` agent's context file and what was last written to it.
#[derive(Component, Default)]
struct Mirror {
    path: PathBuf,
    written: String,
    /// Each message in the file, by id, with the text it was written as.
    blocks: Vec<(MessageId, String)>,
}

impl Plugin for CtxModePlugin {
    fn build(&self, app: &mut App) {
        let mode = match std::env::var("CTX_MODE").as_deref() {
            Ok("keep_last") => Mode::KeepLast,
            Ok("clm") => Mode::Clm,
            _ => Mode::Summary,
        };
        let budget = std::env::var("CTX_BUDGET").ok().and_then(|b| b.parse().ok()).unwrap_or(32_000);
        app.insert_resource(Config { mode, budget })
            .add_systems(AgentPass, shrink_window.in_set(PrepareSystems::Adapt));
        match mode {
            Mode::Summary => {}
            Mode::KeepLast => {
                app.add_systems(AgentPass, keep_last.in_set(PrepareSystems::Edit));
            }
            Mode::Clm => {
                app.add_observer(mirror_agent)
                    .add_systems(AgentPass, sync.in_set(PrepareSystems::Edit));
            }
        }
    }
}

/// Every call's model is given a window of `budget` plus the default
/// compaction reserve, so the default compaction acts at `budget` in
/// `summary` mode, and [`FALLBACK_MARGIN`] past it in the others, where it
/// only catches what their own cut missed.
fn shrink_window(mut calls: Query<&mut Adapted, Added<Adapted>>, config: Res<Config>) {
    let fallback = if config.mode == Mode::Summary { 0 } else { FALLBACK_MARGIN };
    let window = u32::try_from(config.budget + RESERVE + fallback).unwrap_or(u32::MAX);
    for mut adapted in &mut calls {
        let mut spec = (*adapted.0.spec).clone();
        spec.context_window = Some(spec.context_window.map_or(window, |w| w.min(window)));
        spec.max_input_tokens = Some(spec.max_input_tokens.map_or(window, |w| w.min(window)));
        adapted.0.spec = std::sync::Arc::new(spec);
    }
}

/// What a call that sends `conversation` with `request`'s prompt and tools
/// starts with, and its tokens: anchored on the agent's last reported usage
/// when `sent`, the start of the call that reply answered, still holds,
/// else the whole request's estimate (rig-memory's `next_request_tokens`).
fn measure(
    conversation: &Conversation,
    request: &ModelRequest,
    last: &LastUsage,
    sent: Option<RequestStart>,
) -> (u64, u64, RequestStart) {
    let prompt = request.prompt.as_ref().map(|p| p.text());
    let prepared = Prepared { model: "", prompt: prompt.as_deref(), tools: &request.request.tools, history: conversation };
    let whole = prepared.whole_tokens();
    (next_request_tokens(last.0.as_ref(), sent, &prepared), whole, prepared.start())
}

/// Records that `conversation` was changed by this plugin as `kind`.
fn mark(conversation: &mut Conversation, kind: &str) {
    let after = conversation.entries().last().map(|e| e.id);
    conversation.add_aside(Aside::new(kind, Author::plugin(NAME), serde_json::Value::Null).after(after));
}

/// `keep_last`: when the request reaches the budget, every message but the
/// first (the task) and the newest half of the budget is taken out, with no
/// summary.
fn keep_last(
    mut calls: Query<(&mut ModelRequest, &ForAgent), (Added<Admitted>, With<TurnCall>)>,
    mut agents: Query<(&mut Conversation, &LastUsage)>,
    mut sent: Local<HashMap<Entity, RequestStart>>,
    config: Res<Config>,
) {
    for (mut request, &ForAgent(agent)) in &mut calls {
        let Ok((mut conversation, last)) = agents.get_mut(agent) else { continue };
        let (used, whole, start) = measure(&conversation, &request, last, sent.get(&agent).copied());
        sent.insert(agent, start);
        if used < config.budget {
            continue;
        }
        let messages = conversation.messages();
        // The prompt and tools, which every request sends.
        let mut kept = whole.saturating_sub(estimate_tokens(messages));
        let mut from = messages.len();
        while from > 1 {
            kept += estimate_tokens(&messages[from - 1..from]);
            if kept > config.budget / 2 {
                break;
            }
            from -= 1;
        }
        // The newest message stays, however long.
        let from = from.min(messages.len().saturating_sub(1));
        if from <= 1 {
            continue;
        }
        conversation.splice(1..from, [], Author::plugin(NAME));
        conversation.normalize(Author::plugin(NAME));
        mark(&mut conversation, "ctx_mode.cut");
        request.history = Some(conversation.request_view());
        sent.insert(agent, measure(&conversation, &request, last, None).2);
    }
}

/// Gives each new agent its context file and a prompt section naming it.
fn mirror_agent(
    added: On<Add<Agent>>,
    agents: Query<&AgentId>,
    root: Option<Res<ProjectRoot>>,
    config: Res<Config>,
    mut commands: Commands,
) {
    let Ok(id) = agents.get(added.entity) else { return };
    let root = root.map(|r| r.0.clone()).unwrap_or_else(|| PathBuf::from("."));
    let path = root.join(".clm").join(format!("{}.md", id.0));
    let text = format!(
        "Your context is a file you can edit: {path}. It holds this conversation, one block per message, \
         each starting with a `[[CTX <id> <role>]]` line. Before each of your model calls, the file becomes \
         your context: delete a block to forget it, rewrite a block's text to shorten or summarize it, or add a \
         block whose header is `[[CTX new note]]` to keep notes. Edit it with your normal tools (a short python3 \
         script is easiest); messages written after your last edit are always kept. Your context budget is {} \
         tokens. Keep what the task still needs, word for word when it may be asked for exactly (or save it to \
         a file first), and drop what it no longer needs.",
        config.budget,
        path = path.display()
    );
    commands.entity(added.entity).insert(Mirror { path, ..Default::default() });
    commands.spawn((
        Name::new("prompt:clm"),
        SectionOf(added.entity),
        PromptSection::new(PromptSection::ORDER_PROJECT + 60, "context_file", text),
    ));
}

fn role(message: &Msg) -> &'static str {
    match message {
        Msg::User { content } if content.iter().any(|c| matches!(c, UserContent::ToolResult(_))) => "tool",
        Msg::User { .. } => "user",
        Msg::Assistant(_) => "assistant",
        Msg::System { .. } => "system",
    }
}

/// A message as the file shows it. Reasoning is not shown.
fn render(message: &Msg) -> String {
    let mut out = Vec::new();
    match message {
        Msg::System { content } => out.push(content.clone()),
        Msg::User { content } => {
            for c in content {
                match c {
                    UserContent::Text(t) => out.push(t.text().to_owned()),
                    UserContent::ToolResult(r) => {
                        for part in &r.content {
                            match part {
                                ToolResultContent::Text(t) => out.push(t.text().to_owned()),
                                ToolResultContent::Json { value } => out.push(value.to_string()),
                                ToolResultContent::Image(_) => out.push("[image]".to_owned()),
                            }
                        }
                    }
                    _ => out.push("[attachment]".to_owned()),
                }
            }
        }
        Msg::Assistant(a) => {
            for c in &a.content {
                match c {
                    AssistantContent::Text(t) => out.push(t.text().to_owned()),
                    AssistantContent::ToolCall(call) => out.push(call_line(call)),
                    _ => {}
                }
            }
        }
    }
    out.join("\n")
}

/// A tool call as the file shows it, on one line.
fn call_line(call: &ToolCall) -> String {
    let args = serde_json::Value::Object(call.function.arguments.clone());
    format!("<<call {} {args}>>", call.function.name.as_str())
}

/// `message` with its text replaced by `body`: a tool result keeps its call and
/// takes `body` as its output; an assistant message keeps the calls whose
/// lines `body` still holds. `None` when nothing is left.
fn rewritten(message: &Msg, body: &str) -> Option<Msg> {
    match message {
        Msg::User { content } => {
            if let Some(UserContent::ToolResult(r)) = content.iter().find(|c| matches!(c, UserContent::ToolResult(_))) {
                let mut r = r.clone();
                r.content = vec![ToolResultContent::text(body)];
                return Some(Msg::User { content: vec![UserContent::ToolResult(r)] });
            }
            (!body.trim().is_empty()).then(|| Msg::user(body))
        }
        Msg::System { .. } => (!body.trim().is_empty()).then(|| Msg::user(body)),
        Msg::Assistant(a) => {
            let mut a = a.clone();
            let calls: Vec<AssistantContent> = a
                .content
                .iter()
                .filter(|c| matches!(c, AssistantContent::ToolCall(call) if body.contains(&call_line(call))))
                .cloned()
                .collect();
            let text = body.lines().filter(|l| !l.starts_with("<<call ")).collect::<Vec<_>>().join("\n");
            a.content = calls;
            if !text.trim().is_empty() {
                a.content.insert(0, AssistantContent::text(text));
            }
            (!a.content.is_empty()).then_some(Msg::Assistant(a))
        }
    }
}

/// The file's blocks: (id, or `None` for a new one; body).
fn parse(text: &str) -> Vec<(Option<u64>, String)> {
    let mut blocks: Vec<(Option<u64>, String)> = Vec::new();
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("[[CTX ") {
            let id = rest.split_whitespace().next().and_then(|w| w.parse().ok());
            blocks.push((id, String::new()));
        } else if let Some((_, body)) = blocks.last_mut() {
            if !body.is_empty() {
                body.push('\n');
            }
            body.push_str(line);
        }
    }
    blocks
}

fn write(conversation: &Conversation, mirror: &mut Mirror) {
    let mut text = String::new();
    mirror.blocks.clear();
    for entry in conversation.entries() {
        let body = render(entry.message);
        text.push_str(&format!("[[CTX {} {}]]\n{}\n", entry.id.get(), role(entry.message), body));
        mirror.blocks.push((entry.id, body));
    }
    if let Some(dir) = mirror.path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if std::fs::write(&mirror.path, &text).is_ok() {
        mirror.written = text;
    }
}

/// The conversation the agent's edited file `on_disk` describes: a deleted
/// block's message is gone, a rewritten one replaces its message, a block
/// without a known id becomes a note, and messages made since the file was
/// written stay, in order.
fn apply_edits(conversation: &mut Conversation, mirror: &Mirror, on_disk: &str) {
    let index: HashMap<u64, usize> = conversation.entries().map(|e| (e.id.get(), e.index)).collect();
    let known: HashMap<u64, &String> = mirror.blocks.iter().map(|(id, b)| (id.get(), b)).collect();
    let mut entries = Vec::new();
    for (id, body) in parse(on_disk) {
        let body = body.trim_end();
        let found = id.and_then(|id| Some((*index.get(&id)?, *known.get(&id)?)));
        if let Some((at, written)) = found {
            let Some(mut entry) = conversation.entry(at) else { continue };
            if written.trim_end() != body {
                // A message emptied by the edit is dropped: providers reject empty ones.
                let Some(message) = rewritten(&entry.message, body) else { continue };
                entry.message = message.into();
                entry.author = Some(Author::plugin(NAME));
            }
            entries.push(entry);
        } else if !body.trim().is_empty() {
            entries.push(Entry::new(Msg::user(format!("[note]\n{body}"))).by(Author::plugin(NAME)));
        }
    }
    let new = conversation.entries().filter(|e| !known.contains_key(&e.id.get())).map(|e| e.index).collect::<Vec<_>>();
    entries.extend(new.into_iter().filter_map(|at| conversation.entry(at)));
    conversation.set_entries(entries, Author::plugin(NAME));
    conversation.normalize(Author::plugin(NAME));
    mark(conversation, "ctx_mode.edit");
}

/// `clm`: before each turn call, the agent's edits of its file become its
/// conversation; then the file is written afresh.
fn sync(
    mut calls: Query<(&mut ModelRequest, &ForAgent), (Added<Admitted>, With<TurnCall>)>,
    mut agents: Query<(&mut Conversation, &mut Mirror, &LastUsage)>,
    mut sent: Local<HashMap<Entity, RequestStart>>,
    config: Res<Config>,
) {
    for (mut request, &ForAgent(agent)) in &mut calls {
        let Ok((mut conversation, mut mirror, last)) = agents.get_mut(agent) else { continue };
        let on_disk = std::fs::read_to_string(&mirror.path).unwrap_or_default();
        if !mirror.written.is_empty() && on_disk != mirror.written {
            apply_edits(&mut conversation, &mirror, &on_disk);
            request.history = Some(conversation.request_view());
        }
        write(&conversation, &mut mirror);
        let (tokens, _, start) = measure(&conversation, &request, last, sent.get(&agent).copied());
        sent.insert(agent, start);
        if tokens + REMIND_BEFORE >= config.budget
            && let Some(history) = request.history.as_mut()
        {
            let note = format!(
                "[context] About {tokens} of your {} token budget is used. Edit {} now to drop or shorten what \
                 you no longer need; otherwise older messages will be summarized for you.",
                config.budget,
                mirror.path.display()
            );
            history.push(Entry::new(Msg::user(note)).by(Author::plugin(NAME)));
        }
    }
}
