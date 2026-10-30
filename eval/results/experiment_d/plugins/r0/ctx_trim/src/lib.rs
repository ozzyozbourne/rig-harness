//! `ctx_trim`: cut the bulk of big tool outputs out of the agent's context file
//! (the `.clm/*.md` mirror) while keeping the lines that matter, without ever
//! touching a block header or an assistant message (whose `<<call ..>>` lines
//! must survive).

use rig_harness::prelude::*;
use schemars::JsonSchema;
use serde::Deserialize;
use rig_harness::rig_core::tool::ToolErrorKind;

/// Listed in plugins.toml; the agent adds it with `Default`.
#[derive(Default)]
pub struct CtxTrimPlugin;

impl Plugin for CtxTrimPlugin {
    fn build(&self, app: &mut App) {
        app.add_tool_with(
            Trim,
            ToolOptions {
                footprint: Some(Touches::Reads(Scope::Anything)),
            },
        );
    }
}

struct Trim;

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Args {
    /// The context file to edit, such as `/tmp/run/.clm/<id>.md`.
    path: String,
    /// Keep every line of a trimmed output that contains one of these
    /// substrings, in order (for example `KEY_` and `NEXT FILE`). Empty drops
    /// the whole output.
    #[serde(default)]
    keep: Vec<String>,
    /// Only a block body longer than this many bytes is trimmed.
    #[serde(default = "default_max")]
    max_bytes: usize,
    /// A fact to add at the end as a `[[CTX new note]]` block, kept as is.
    #[serde(default)]
    note: Option<String>,
    /// Report what would change and write nothing.
    #[serde(default)]
    dry_run: bool,
}

fn default_max() -> usize {
    2000
}

/// A block holds the note when its header names a note, or its first body line
/// is the `[note]` marker.
fn is_note_block(header: &str, body: &[&str]) -> bool {
    if header.contains(" note]]") || header.contains(" new note]]") {
        return true;
    }
    header.ends_with(" note]]") || body.first().map(|l| l.trim() == "[note]").unwrap_or(false)
}

impl Tool for Trim {
    const NAME: &'static str = "ctx_trim";
    type Args = Args;
    type Output = String;
    type Error = ToolExecutionError;

    fn description(&self) -> String {
        "Trim big `tool` output blocks of the agent's context file down to the \
         lines matching `keep`, preserving every block header, then optionally \
         add the note in place of every existing note block. Use right after a \
         large read instead of editing the file by hand with line indices."
            .to_string()
    }

    fn parameters(&self) -> serde_json::Value {
        args_schema::<Args>()
    }

    async fn call(&self, _: &mut ToolContext, args: Args) -> Result<String, ToolExecutionError> {
        let text = std::fs::read_to_string(&args.path)
            .map_err(|e| ToolExecutionError::new(ToolErrorKind::InvalidArgs, format!("reading {}: {e}", args.path)))?;
        let (total, report) = rewrite(&text, &args.keep, args.max_bytes, args.note.as_deref(), args.dry_run);

        if args.dry_run {
            return Ok(format!(
                "dry run, nothing written. file is {} bytes.\n{}",
                text.len(),
                report.join("\n")
            ));
        }
        std::fs::write(&args.path, &total)
            .map_err(|e| ToolExecutionError::new(ToolErrorKind::InvalidArgs, format!("writing {}: {e}", args.path)))?;
        Ok(format!(
            "{} -> {} bytes (was {}).\n{}",
            args.path,
            total.len(),
            text.len(),
            report.join("\n")
        ))
    }
}

/// The whole walk `call` does, without the file system: split into blocks, trim
/// every big `tool` block to the lines matching `keep`, drop the note blocks and
/// put `note` in their place, and report what happened.
fn rewrite(
    text: &str,
    keep: &[String],
    max_bytes: usize,
    note: Option<&str>,
    dry_run: bool,
) -> (String, Vec<String>) {
    let lines: Vec<&str> = text.lines().collect();

    // Split into blocks: a `[[CTX ` line opens one; anything before the
    // first header is preamble and kept as is.
    let mut out: Vec<String> = Vec::new();
    let mut report: Vec<String> = Vec::new();
    // A note set here replaces the note blocks already in the file, so
    // calling this tool after every read cannot pile up copies.
    let want_note = note.map(|n| !n.trim().is_empty()).unwrap_or(false);
    let mut replaced = 0usize;
    let mut i = 0;
    while i < lines.len() {
        let header = lines[i];
        if !header.starts_with("[[CTX ") {
            out.push(header.to_string());
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < lines.len() && !lines[j].starts_with("[[CTX ") {
            j += 1;
        }
        let body = &lines[i + 1..j];
        let trimmed_body: String = body.join("\n");
        if want_note && is_note_block(header, body) {
            replaced += 1;
            i = j;
            continue;
        }
        let is_tool = header.ends_with(" tool]]");
        if is_tool && trimmed_body.len() > max_bytes {
            let kept: Vec<&str> = trimmed_body
                .lines()
                .filter(|line| keep.iter().any(|k| !k.is_empty() && line.contains(k)))
                .collect();
            let marker = format!(
                "[ctx_trim: {} bytes of output dropped, {} line(s) kept]",
                trimmed_body.len(),
                kept.len()
            );
            let mut fresh = vec![marker];
            fresh.extend(kept.into_iter().map(str::to_string));
            if !dry_run {
                out.push(header.to_string());
                out.extend(fresh.clone());
            }
            report.push(format!("{}: {} bytes -> {}", header, trimmed_body.len(), fresh.join(" / ")));
        } else {
            out.push(header.to_string());
            out.extend(body.iter().map(|l| l.to_string()));
            if is_tool && !trimmed_body.is_empty() {
                report.push(format!("{header}: {} bytes, left alone", trimmed_body.len()));
            }
        }
        i = j;
    }

    if let Some(note) = note.filter(|n| !n.trim().is_empty()) {
        out.push("[[CTX new note]]".to_string());
        out.push(note.trim().to_string());
        if replaced > 0 {
            report.push(format!(
                "note set: {} bytes, {replaced} old note block(s) replaced",
                note.trim().len()
            ));
        } else {
            report.push(format!("note added: {} bytes", note.trim().len()));
        }
    }

    (out.join("\n"), report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keep(words: &[&str]) -> Vec<String> {
        words.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn keeps_headers_and_kept_lines_and_leaves_assistant_alone() {
        let text = "[[CTX 1 user]]\nask\n[[CTX 2 tool]]\nnoise a\nKEY_0 = X\nnoise b\nNEXT FILE: y\n[[CTX 3 assistant]]\n<<call ctx_trim {\"path\":\"p\"}>>\nsome words";
        let (got, _) = rewrite(text, &keep(&["KEY_", "NEXT FILE"]), 10, None, false);
        assert!(got.contains("[[CTX 2 tool]]\n[ctx_trim:"));
        assert!(got.contains("KEY_0 = X\nNEXT FILE: y"));
        assert!(!got.contains("noise a"));
        assert!(got.contains("[[CTX 3 assistant]]\n<<call ctx_trim {\"path\":\"p\"}>>\nsome words"));
    }

    #[test]
    fn a_note_replaces_the_notes_already_there() {
        let text = "[[CTX 1 user]]\nask\n[[CTX new note]]\nold store\n[[CTX 2 note]]\nold facts\n[[CTX 3 assistant]]\nwords";
        let (got, report) = rewrite(text, &[], 100, Some("STORE k0=1"), false);
        assert!(got.contains("[[CTX new note]]\nSTORE k0=1"));
        assert!(!got.contains("old store") && !got.contains("old facts"));
        assert_eq!(got.matches("note]]").count(), 1, "got: {got}");
        assert!(report.iter().any(|r| r.contains("2 old note block(s) replaced")), "{report:?}");
        assert!(got.contains("[[CTX 1 user]]\nask") && got.contains("[[CTX 3 assistant]]\nwords"));
    }
}
