//! The `utc_clock` plugin of the rig agent, made by `rig plugin new`.
//!
//! Adds the `utc_now` tool: the current UTC time as RFC 3339 text, from
//! `std::time::SystemTime` alone. The plugin guide, rig-harness's
//! `PLUGINS.md` (its `plugin_guide` docs), has the rules every plugin
//! follows; every name here comes from `rig_harness::prelude`.

use std::time::{SystemTime, UNIX_EPOCH};

use rig_harness::prelude::*;
use schemars::JsonSchema;
use serde::Deserialize;

/// Listed in plugins.toml; the agent adds it with `Default`.
#[derive(Default)]
pub struct UtcClockPlugin;

impl Plugin for UtcClockPlugin {
    fn build(&self, app: &mut App) {
        // `utc_now` only reads the clock, so every call is safe to run
        // again once stopped, and safe beside any other call.
        app.add_tool_with(
            UtcNow,
            ToolOptions {
                footprint: Some(Touches::Reads(Scope::Anything)),
            },
        );
    }
}

/// The `utc_now` tool: no arguments, the current UTC time as RFC 3339 text.
#[derive(Default)]
struct UtcNow;

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct NoArgs {}

impl Tool for UtcNow {
    const NAME: &'static str = "utc_now";
    type Args = NoArgs;
    type Output = String;
    type Error = ToolExecutionError;

    fn description(&self) -> String {
        "Returns the current UTC time as RFC 3339 text, such as \
         2026-07-14T12:34:56.123Z. Use it whenever the date or the time of \
         day matters, rather than guessing it or running `date` in `shell`."
            .to_owned()
    }

    fn parameters(&self) -> serde_json::Value {
        args_schema::<NoArgs>()
    }

    async fn call(&self, _: &mut ToolContext, _: NoArgs) -> Result<String, ToolExecutionError> {
        Ok(rfc3339_now())
    }
}

/// The current UTC time, from `SystemTime`, as `YYYY-MM-DDTHH:MM:SS[.fff]Z`.
fn rfc3339_now() -> String {
    // Before the epoch keeps its signed seconds; `duration_since` only
    // fails on a clock set earlier than 1970, which then reads as the epoch.
    let since_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    rfc3339(since_epoch.as_secs() as i64, since_epoch.subsec_nanos())
}

/// Seconds and nanoseconds since the epoch, as `YYYY-MM-DDTHH:MM:SS[.fff]Z`.
fn rfc3339(secs: i64, nanos: u32) -> String {
    let (year, month, day) = civil_from_days(secs.div_euclid(86_400));
    let second_of_day = secs.rem_euclid(86_400) as u32;
    let (hour, minute, second) = (
        second_of_day / 3_600,
        second_of_day % 3_600 / 60,
        second_of_day % 60,
    );
    let mut text = format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}");
    // Fractional seconds are optional in RFC 3339; trimmed of trailing zeros.
    if nanos > 0 {
        let mut fraction = format!("{nanos:09}");
        while fraction.ends_with('0') {
            fraction.pop();
        }
        text.push('.');
        text.push_str(&fraction);
    }
    text.push('Z');
    text
}

/// Days since 1970-01-01 to a (year, month, day) in the proleptic Gregorian
/// calendar (Howard Hinnant's `civil_from_days`).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = (z - era * 146_097) as u64; // [0, 146096]
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365; // [0, 399]
    let year = year_of_era as i64 + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100); // [0, 365]
    let mp = (5 * day_of_year + 2) / 153; // [0, 11]
    let day = (day_of_year - (153 * mp + 2) / 5 + 1) as u32; // [1, 31]
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32; // [1, 12]
    (year + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn epoch_is_1970_01_01() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(rfc3339(0, 0), "1970-01-01T00:00:00Z");
    }

    #[test]
    fn known_dates() {
        assert_eq!(civil_from_days(19_000), (2022, 1, 8));
        assert_eq!(civil_from_days(-1), (1969, 12, 31));
        assert_eq!(civil_from_days(20_652), (2026, 7, 18));
        // 2024-02-29, a leap day.
        assert_eq!(civil_from_days(19_782), (2024, 2, 29));
    }

    #[test]
    fn fractions_trim_and_vanish() {
        assert_eq!(rfc3339(1_752_498_296, 0), "2025-07-14T13:04:56Z");
        assert_eq!(
            rfc3339(1_752_498_296, 123_000_000),
            "2025-07-14T13:04:56.123Z"
        );
        assert_eq!(
            rfc3339(1_752_498_296, 1_000_000),
            "2025-07-14T13:04:56.001Z"
        );
    }
}
