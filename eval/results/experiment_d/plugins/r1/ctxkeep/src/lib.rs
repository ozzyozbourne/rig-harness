//! The `ctxkeep` plugin: a `ctx_edit` tool that trims an agent's context file
//! by dropping named blocks, so a whole-file rewrite (which once lost the
//! user's instructions) is never needed. Blocks whose role is `user` are
//! never dropped, and a `drop` id that matches nothing refuses the edit.

use std::{fs, path::Path};

use rig_harness::prelude::*;
use rig_harness::rig_tools::blocking;
use schemars::JsonSchema;
use serde::Deserialize;

/// Listed in plugins.toml; the agent adds it with `Default`.
#[derive(Default)]
pub struct CtxkeepPlugin;

impl Plugin for CtxkeepPlugin {
    fn build(&self, app: &mut App) {
        app.add_tool(CtxEdit)
            .add_observer(name_tool::<CtxEdit>);
    }
}

/// Tells each agent that `ctx_edit` is how its context file is trimmed: the
/// tool is useless if the prompt points at a script instead.
fn name_tool<T: Tool>(added: On<Add<Agent>>, mut commands: Commands) {
    let text = format!(
        "Trim your context file with the `{}` tool rather than a script: `list` gives each \
         block's id and size, `drop` takes ids, `note` sets the `[[CTX new note]]` block. In one \
         call, record what you may be asked for word for word in the note and drop the tool block \
         it came from; `user` blocks (the task's instructions) are never dropped.",
        T::NAME
    );
    commands.spawn((
        Name::new(format!("prompt:{}", T::NAME)),
        SectionOf(added.entity),
        PromptSection::new(PromptSection::ORDER_PROJECT + 61, "ctx_edit", text),
    ));
}

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Args {
    /// The context file to edit.
    path: String,
    /// Ids (or id prefixes) of blocks to remove. A `user` block is refused.
    #[serde(default)]
    drop: Vec<String>,
    /// Text for the `[[CTX new note]]` block: replaces its body, or adds one.
    note: Option<String>,
    /// Only list the blocks with their ids, sizes and first line.
    #[serde(default)]
    list: bool,
}

struct Block {
    header: String,
    id: String,
    role: String,
    body: Vec<String>,
}

fn parse(text: &str) -> (Vec<String>, Vec<Block>) {
    let mut pre = Vec::new();
    let mut blocks: Vec<Block> = Vec::new();
    for line in text.lines() {
        if line.starts_with("[[CTX ") {
            let inner = line
                .trim_start_matches("[[CTX ")
                .trim_end_matches("]]")
                .trim();
            let mut parts = inner.split_whitespace();
            let id = parts.next().unwrap_or("").to_string();
            let role = parts.next().unwrap_or("").to_string();
            blocks.push(Block { header: line.to_string(), id, role, body: Vec::new() });
        } else if let Some(last) = blocks.last_mut() {
            last.body.push(line.to_string());
        } else {
            pre.push(line.to_string());
        }
    }
    (pre, blocks)
}

/// The file's text with the `drops` removed and `note` as its last note
/// block; `Err` says why nothing is to be written.
fn edit(text: &str, drops: &[String], note: Option<&str>) -> Result<String, String> {
    let (pre, blocks) = parse(text);
    let mut drop = vec![false; blocks.len()];
    for want in drops {
        let mut hit = false;
        for (i, b) in blocks.iter().enumerate() {
            if b.id.starts_with(want.as_str()) {
                hit = true;
                if b.role == "user" {
                    return Err(format!(
                        "block {} is a `user` block: it holds the task's instructions and is never dropped",
                        b.id
                    ));
                }
                drop[i] = true;
            }
        }
        if !hit {
            return Err(format!(
                "no block id starts with `{want}`: nothing changed, list the blocks first"
            ));
        }
    }

    let mut out = String::new();
    for line in &pre {
        out.push_str(line);
        out.push('\n');
    }
    for (i, b) in blocks.iter().enumerate() {
        if drop[i] {
            continue;
        }
        out.push_str(&b.header);
        out.push('\n');
        for line in &b.body {
            out.push_str(line);
            out.push('\n');
        }
    }
    if let Some(note) = note {
        let header = "[[CTX new note]]";
        let mut kept = String::new();
        let mut skipping = false;
        for line in out.lines() {
            if line.starts_with("[[CTX ") {
                skipping = line.trim() == header;
            }
            if !skipping {
                kept.push_str(line);
                kept.push('\n');
            }
        }
        kept.push_str(&format!("{header} {note}\n"));
        out = kept;
    }
    Ok(out)
}

/// A `ctx_edit` tool: an agent's context file, trimmed by ids.
struct CtxEdit;

impl Tool for CtxEdit {
    const NAME: &'static str = "ctx_edit";
    type Args = Args;
    type Output = String;
    type Error = ToolExecutionError;

    fn description(&self) -> String {
        "List or trim an agent's context file block by block, dropping the blocks \
         named by id instead of rewriting the file. Use it rather than `write` or a \
         script to forget context: `user` blocks (the instructions) are never dropped, \
         and an id that matches nothing refuses the whole edit with the file untouched."
            .to_owned()
    }

    fn parameters(&self) -> serde_json::Value {
        args_schema::<Args>()
    }

    async fn call(&self, _: &mut ToolContext, args: Args) -> Result<String, ToolExecutionError> {
        blocking(move || {
            let path = Path::new(&args.path);
            let text = fs::read_to_string(path)
                .map_err(|e| ToolExecutionError::other(format!("{}: {e}", args.path)))?;
            let (_, blocks) = parse(&text);

            if args.list {
                let mut out = format!(
                    "{} blocks, ~{} tokens\n",
                    blocks.len(),
                    text.len() / 4
                );
                for (i, b) in blocks.iter().enumerate() {
                    let chars: usize = b.body.iter().map(|l| l.len() + 1).sum();
                    let first = b
                        .body
                        .iter()
                        .find(|l| !l.trim().is_empty())
                        .map(|l| l.trim())
                        .unwrap_or("");
                    let first: String = first.chars().take(70).collect();
                    out.push_str(&format!(
                        "{i}: id={} role={} ~{} tok | {first}\n",
                        b.id,
                        b.role,
                        chars / 4
                    ));
                }
                return Ok(out);
            }

            let out =
                edit(&text, &args.drop, args.note.as_deref()).map_err(ToolExecutionError::refused)?;
            let tmp = path.with_extension("ctxkeep.tmp");
            fs::write(&tmp, &out)
                .and_then(|_| fs::rename(&tmp, path))
                .map_err(|e| ToolExecutionError::other(format!("write {}: {e}", args.path)))?;
            let (_, left) = parse(&out);
            Ok(format!(
                "ok: dropped [{}], {} blocks left, ~{} tokens.",
                args.drop.join(", "),
                left.len(),
                out.len() / 4
            ))
        })
        .await
    }
}
