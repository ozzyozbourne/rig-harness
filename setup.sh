#!/usr/bin/env bash
# Installs this harness: rig at the tested commit, its launcher, and these
# plugins in RIG_HOME. Rerunnable.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RIG_COMMIT=454887eeeeb64ecca6e26afe05edbbc924b07842
source "$HERE/rig-env.sh" 2>/dev/null || true
export RIG_HOME="${RIG_HOME:-$HOME/.rig}" RIG_SOURCE="${RIG_SOURCE:-$HOME/rig}"

command -v cargo >/dev/null || { curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal; source ~/.cargo/env; }
if [ ! -d "$RIG_SOURCE/.git" ]; then
  git clone https://github.com/0xPlaygrounds/rig.git "$RIG_SOURCE"
fi
git -C "$RIG_SOURCE" fetch -q origin "$RIG_COMMIT" 2>/dev/null || true
git -C "$RIG_SOURCE" checkout -q "$RIG_COMMIT"
cargo install --locked --path "$RIG_SOURCE" --root "$HOME/.rig-bin"

mkdir -p "$RIG_HOME/plugins"
for p in "$HERE"/plugins/*/; do
  name="$(basename "$p")"
  rm -rf "$RIG_HOME/plugins/$name"; cp -r "$p" "$RIG_HOME/plugins/$name"
  sed -i.bak "s#@RIG_SOURCE@#$RIG_SOURCE#g" "$RIG_HOME/plugins/$name/Cargo.toml" && rm "$RIG_HOME/plugins/$name/Cargo.toml.bak"
done
cp "$HERE/plugins.toml" "$RIG_HOME/plugins.toml"
"$HOME/.rig-bin/bin/rig" build
echo "Done. Export OPENROUTER_API_KEY, source rig-env.sh, then: rig -m openrouter/z-ai/glm-5.3-flash"
