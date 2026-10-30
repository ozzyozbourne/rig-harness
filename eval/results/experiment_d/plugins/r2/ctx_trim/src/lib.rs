//! The `ctx_trim` plugin of the rig agent.
//!
//! Adds the `ctx_trim` tool: shrinks the big tool-output blocks of a context
//! file in one call, keeping only the lines that match a pattern and never
//! leaving a stray `[[CTX ...]]` header inside a block body. Every name here
//! comes from `rig_harness::prelude`; the guide is rig-harness's `PLUGINS.md`.

use std::fs;

use rig_harness::prelude::*;
use schemars::JsonSchema;
use serde::Deserialize;

/// Listed in plugins.toml; the agent adds it with `Default`.
#[derive(Default)]
pub struct CtxTrimPlugin;

impl Plugin for CtxTrimPlugin {
    fn build(&self, app: &mut App) {
        // `ctx_trim` rewrites one file the agent names, so it touches the
        // filesystem anywhere; it is safe to run again once stopped.
        app.add_tool_with(
            CtxTrim,
            ToolOptions {
                footprint: Some(Touches::Writes(Scope::Anything)),
            },
        );
    }
}

/// The `ctx_trim` tool: collapse tool blocks longer than a line limit.
#[derive(Default)]
struct CtxTrim;

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Args {
    /// The context file to edit, such as `~/.clm/<id>.md` or a full path.
    path: String,
    /// Keep only body lines containing one of these `|`-separated texts.
    /// Empty keeps nothing but the block header and a line count.
    #[serde(default)]
    keep: String,
    /// Only blocks whose body has more than this many lines are trimmed.
    #[serde(default = "default_min_lines")]
    min_lines: usize,
}

fn default_min_lines() -> usize {
    12
}

impl Tool for CtxTrim {
    const NAME: &'static str = "ctx_trim";
    type Args = Args;
    type Output = String;
    type Error = ToolExecutionError;

    fn description(&self) -> String {
        "Shrinks a context file in place: every `[[CTX n tool]]` block whose \
         body is longer than `min_lines` is cut down to the body lines that \
         contain one of the `|`-separated texts in `keep`, or to a line count \
         when `keep` is empty. Kept lines never start with `[[CTX`, so no \
         stray block header appears. Call it right after a big file read, \
         keeping a short identifying fragment (a key, a name, an error line) \
         rather than dropping the whole output. Returns the block count, the \
         bytes freed and the lines kept."
            .to_owned()
    }

    fn parameters(&self) -> serde_json::Value {
        args_schema::<Args>()
    }

    async fn call(&self, _: &mut ToolContext, args: Args) -> Result<String, ToolExecutionError> {
        let path = expand_tilde(&args.path);
        let text = fs::read_to_string(&path).map_err(|error| {
            ToolExecutionError::other(format!("reading {}: {error}", path.display()))
        })?;
        let needles: Vec<&str> = args
            .keep
            .split('|')
            .map(str::trim)
            .filter(|needle| !needle.is_empty())
            .collect();
        let (trimmed, blocks, kept) = collapse(&text, &needles, args.min_lines);
        if trimmed != text {
            fs::write(&path, &trimmed).map_err(|error| {
                ToolExecutionError::other(format!("writing {}: {error}", path.display()))
            })?;
        }
        Ok(format!(
            "{}: trimmed {blocks} blocks, {} bytes now ({} before); kept lines: {}",
            path.display(),
            trimmed.len(),
            text.len(),
            if kept.is_empty() {
                "none".to_owned()
            } else {
                kept.join(" / ")
            }
        ))
    }
}

/// `~` at the start of a path means the home directory.
fn expand_tilde(path: &str) -> std::path::PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        let home = std::env::var("HOME").unwrap_or_default();
        return std::path::Path::new(&home).join(rest);
    }
    std::path::PathBuf::from(path)
}

/// Replace each long tool block's body with its `keep` lines; returns the new
/// text, the number of blocks trimmed and the lines kept.
fn collapse(text: &str, needles: &[&str], min_lines: usize) -> (String, usize, Vec<String>) {
    let mut out = String::with_capacity(text.len());
    let mut blocks = 0;
    let mut kept = Vec::new();
    // Blocks start at a line beginning `[[CTX `; everything before the first
    // one is preamble and is copied as is.
    let mut header: Option<String> = None;
    let mut body: Vec<&str> = Vec::new();
    let flush = |out: &mut String,
                 header: &Option<String>,
                 body: &mut Vec<&str>,
                 blocks: &mut usize,
                 kept: &mut Vec<String>| {
        let Some(header) = header else {
            out.push_str(&body.join("\n"));
            body.clear();
            return;
        };
        out.push_str(header);
        out.push('\n');
        let is_tool = header.starts_with("[[CTX ") && header.contains(" tool]]");
        let nonempty = body.iter().filter(|line| !line.trim().is_empty()).count();
        if is_tool && nonempty > min_lines {
            *blocks += 1;
            let mut any = false;
            for line in body.iter() {
                let line = line.trim_end();
                if line.trim().is_empty() || line.starts_with("[[CTX") {
                    continue;
                }
                if !needles.is_empty() && needles.iter().any(|needle| line.contains(needle)) {
                    out.push_str(line);
                    out.push('\n');
                    kept.push(line.chars().take(100).collect());
                    any = true;
                }
            }
            out.push_str(&format!("({nonempty} lines trimmed by ctx_trim"));
            if !any {
                out.push_str(", none matched");
            }
            out.push_str(")\n\n");
        } else {
            let body_text = body.join("\n");
            out.push_str(body_text.trim_end_matches('\n'));
            out.push_str("\n\n");
        }
        body.clear();
    };
    for line in text.split('\n') {
        if line.starts_with("[[CTX ") {
            flush(&mut out, &header, &mut body, &mut blocks, &mut kept);
            header = Some(line.to_owned());
        } else if header.is_some() {
            body.push(line);
        } else {
            out.push_str(line);
            out.push('\n');
        }
    }
    flush(&mut out, &header, &mut body, &mut blocks, &mut kept);
    let mut trimmed = out.trim_end().to_owned();
    trimmed.push('\n');
    (trimmed, blocks, kept)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_only_matching_lines() {
        let text = "[[CTX 0 user]]\nask\n\n[[CTX 1 tool]]\nnoise\nKEY_0 = A\nnoise\nmore\nnoise\nnoise\nnoise\nnoise\nnoise\nnoise\nnoise\nnoise\nnoise\n\n[[CTX 2 assistant]]\nreply\n";
        let (out, blocks, kept) = collapse(text, &["KEY_"], 5);
        assert_eq!(blocks, 1);
        assert!(out.contains("KEY_0 = A"));
        assert!(!out.contains("noise"));
        assert!(out.contains("[[CTX 2 assistant]]\nreply"));
        assert_eq!(kept.len(), 1);
    }

    #[test]
    fn never_leaves_a_header_in_a_body() {
        let text = "[[CTX 1 tool]]\na\n[[CTX 9 assistant]]\nb\nc\nd\ne\nf\n";
        let (out, _, _) = collapse(text, &[], 1);
        assert_eq!(out.matches("[[CTX").count(), 2);
    }
}
