//! `ctx_note`: keep exactly ONE note block in the agent's context file.
//!
//! Editing a note with `edit` fails as soon as the same note text appears in
//! more than one block (a note appended by `ctx_trim`, plus copies in earlier
//! messages), and hand scripts drift. This tool replaces every note block with
//! one `[[CTX new note]]` block holding the given text, in a single call; it
//! never touches tool or assistant blocks.

use rig_harness::prelude::*;
use schemars::JsonSchema;
use serde::Deserialize;
use rig_harness::rig_core::tool::ToolErrorKind;

/// Listed in plugins.toml; the agent adds it with `Default`.
#[derive(Default)]
pub struct CtxNotePlugin;

impl Plugin for CtxNotePlugin {
    fn build(&self, app: &mut App) {
        app.add_tool_with(
            Note,
            ToolOptions {
                footprint: Some(Touches::Reads(Scope::Anything)),
            },
        );
    }
}

struct Note;

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Args {
    /// The context file to edit, such as `/tmp/run/.clm/<id>.md`.
    path: String,
    /// The whole note to keep, verbatim: the state it holds is replaced, not
    /// merged. Empty removes every note block.
    #[serde(default)]
    text: String,
    /// Report what would change and write nothing.
    #[serde(default)]
    dry_run: bool,
}

/// A block holds the note when its header names a note, or its first body line
/// is the `[note]` marker.
fn is_note_block(header: &str, body: &[&str]) -> bool {
    if header.contains(" note]]") || header.contains(" new note]]") {
        return true;
    }
    header.ends_with(" note]]") || body.first().map(|l| l.trim() == "[note]").unwrap_or(false)
}

fn apply(text: &str, note: &str) -> (String, usize) {
    let lines: Vec<&str> = text.lines().collect();
    let mut out: Vec<String> = Vec::new();
    let mut dropped = 0usize;
    let mut i = 0;
    while i < lines.len() {
        if !lines[i].starts_with("[[CTX ") {
            out.push(lines[i].to_string());
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < lines.len() && !lines[j].starts_with("[[CTX ") {
            j += 1;
        }
        let body = &lines[i + 1..j];
        if is_note_block(lines[i], body) {
            dropped += 1;
        } else {
            out.push(lines[i].to_string());
            out.extend(body.iter().map(|l| l.to_string()));
        }
        i = j;
    }
    let trimmed = note.trim();
    if !trimmed.is_empty() {
        out.push("[[CTX new note]]".to_string());
        out.push(trimmed.to_string());
    }
    let mut joined = out.join("\n");
    if !joined.ends_with('\n') {
        joined.push('\n');
    }
    (joined, dropped)
}

impl Tool for Note {
    const NAME: &'static str = "ctx_note";
    type Args = Args;
    type Output = String;
    type Error = ToolExecutionError;

    fn description(&self) -> String {
        "Replace every note block of the agent's context file with one \
         `[[CTX new note]]` block holding `text` verbatim (empty clears them). \
         Use it to keep a key-value store or facts in context: one call updates \
         the whole note and duplicates can never make an `edit` ambiguous. Tool \
         and assistant blocks are left alone."
            .to_string()
    }

    fn parameters(&self) -> serde_json::Value {
        args_schema::<Args>()
    }

    async fn call(&self, _: &mut ToolContext, args: Args) -> Result<String, ToolExecutionError> {
        let text = std::fs::read_to_string(&args.path)
            .map_err(|e| ToolExecutionError::new(ToolErrorKind::InvalidArgs, format!("reading {}: {e}", args.path)))?;
        let (fresh, dropped) = apply(&text, &args.text);
        if args.dry_run {
            return Ok(format!(
                "dry run, nothing written. would replace {dropped} note block(s); {} bytes -> {} bytes.",
                text.len(),
                fresh.len()
            ));
        }
        std::fs::write(&args.path, &fresh)
            .map_err(|e| ToolExecutionError::new(ToolErrorKind::InvalidArgs, format!("writing {}: {e}", args.path)))?;
        Ok(format!(
            "{} -> {} bytes (was {}), {dropped} old note block(s) replaced",
            args.path,
            fresh.len(),
            text.len()
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_note_whatever_the_copies() {
        let text = "[[CTX 1 user]]\nask\n[[CTX new note]]\nSTORE k0=1\n[[CTX 2 assistant]]\n<<call x>>\nSTORE k0=1\n[[CTX 3 user]]\n[note]\nSTORE k0=1\n";
        let (got, dropped) = apply(text, "STORE k0=2 k1=9");
        assert_eq!(dropped, 2);
        assert_eq!(got.matches("[[CTX new note]]").count(), 1);
        assert!(got.contains("STORE k0=2 k1=9"));
        assert!(got.contains("[[CTX 1 user]]\nask"));
        assert!(got.contains("<<call x>>"));
        let (cleared, dropped) = apply(&got, "");
        assert_eq!(dropped, 1);
        assert!(!cleared.contains("new note"));
    }
}
