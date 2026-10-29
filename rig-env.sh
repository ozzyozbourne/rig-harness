# Source this before running `rig`. Set RIG_SOURCE to your rig checkout.
source ~/.cargo/env
export PATH="$HOME/.rig-bin/bin:$PATH"
export RIG_HOME="${RIG_HOME:-$HOME/.rig}"
export RIG_SOURCE="${RIG_SOURCE:-$HOME/rig}"
# Keep Rust builds inside the orb's 6 GiB limit: two rustc jobs at a time,
# and no incremental cache (the agent's own crates rarely change; a plugin
# edit rebuilds only that small crate).
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"
export CARGO_INCREMENTAL="${CARGO_INCREMENTAL:-0}"
