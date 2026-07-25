use super::WorkerCount;
use camino::Utf8PathBuf;
use serde::Deserialize;

/// Errors returned by config parsing when structural rules are violated.
///
/// This complements `toml::de::Error` (which handles syntax/type mismatches) by
/// carrying oxitest-specific structural rejections — most notably legacy keys
/// that were removed and now require migration.
#[derive(Debug)]
pub(crate) enum ConfigError {
    /// A key was removed and users must migrate to a replacement location.
    LegacyKey {
        key: &'static str,
        replacement: &'static str,
    },
    /// TOML syntax or type-shape error from serde.
    Toml(toml::de::Error),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::LegacyKey { key, replacement } => write!(
                f,
                "`{key}` is no longer supported; move settings under {replacement} instead",
            ),
            Self::Toml(err) => std::fmt::Display::fmt(err, f),
        }
    }
}

impl std::error::Error for ConfigError {}

impl From<toml::de::Error> for ConfigError {
    fn from(err: toml::de::Error) -> Self {
        Self::Toml(err)
    }
}

/// Reject legacy `[tool.oxitest]` keys that were removed in the doctest rework (#1602).
///
/// Called before serde deserialization so we can produce a helpful migration
/// message pointing at the new sub-table, rather than letting the field be
/// silently ignored or (worse) accepted into a stale field on `OxitestConfig`.
///
/// **Convention:** when removing a field from `OxitestConfig` with a
/// migration path, add a matching `LegacyKey` variant here so users see the
/// specific hint rather than the generic `unknown field 'X'` from
/// `deny_unknown_fields`. Purely removed fields (no migration path) rely on
/// the generic error. See ADR-0008.
pub(crate) fn check_no_legacy_keys(raw: &str) -> Result<(), ConfigError> {
    // Parse to a generic `toml::Value` — cheaper than reflecting on OxitestConfig
    // and independent of the field set we're actively evolving.
    let value: toml::Value = match toml::from_str(raw) {
        Ok(v) => v,
        // Syntax errors are the caller's problem; surface via normal deserialization.
        Err(_) => return Ok(()),
    };
    let Some(oxitest) = value
        .get("tool")
        .and_then(|t| t.get("oxitest"))
        .and_then(|o| o.as_table())
    else {
        return Ok(());
    };
    if oxitest.contains_key("doctest_modules") {
        return Err(ConfigError::LegacyKey {
            key: "tool.oxitest.doctest_modules",
            replacement: "[tool.oxitest.doctest]",
        });
    }
    Ok(())
}

/// Outcome of extracting `[tool.oxitest]` from raw pyproject.toml content.
///
/// The three-variant shape (rather than `Option<OxitestConfig>`) lets
/// `Config::load` distinguish absent-subtable (silent defaults) from
/// whole-file syntax error (warn + defaults). See ADR-0008.
#[derive(Debug)]
#[cfg_attr(
    not(test),
    expect(dead_code, reason = "wired into Config::load in Task 4 of PR #1646")
)]
pub(crate) enum ParseOutcome {
    /// `[tool.oxitest]` parsed successfully.
    Present(OxitestConfig),
    /// Pyproject parses but has no `[tool.oxitest]` sub-table.
    Absent,
    /// Whole-file TOML syntax error — subtable extraction skipped by design.
    WholeFileParseError(
        #[allow(dead_code)] // destructured by Config::load in Task 4; tests match via `matches!`
        toml::de::Error,
    ),
}

/// Extract and validate `[tool.oxitest]` from raw pyproject.toml content.
///
/// Value-based extraction: only errors inside `[tool.oxitest]` reach the
/// caller as `Err`. Whole-file TOML syntax errors and missing/absent
/// `[tool.oxitest]` sub-table are signalled via `ParseOutcome` — see ADR-0008
/// for the narrow-scope rationale.
///
/// Returns:
/// - `Ok(ParseOutcome::Present(config))` — `[tool.oxitest]` parsed successfully.
/// - `Ok(ParseOutcome::Absent)` — pyproject parses, no `[tool.oxitest]` sub-table.
/// - `Ok(ParseOutcome::WholeFileParseError(e))` — whole-file TOML syntax broken;
///   caller decides whether to warn.
/// - `Err(ConfigError::Toml(_))` — `[tool.oxitest]` failed to deserialize
///   (unknown field via `deny_unknown_fields`, wrong type, malformed value).
#[cfg_attr(
    not(test),
    expect(dead_code, reason = "wired into Config::load in Task 4 of PR #1646")
)]
pub(crate) fn parse_oxitest_config(raw: &str) -> Result<ParseOutcome, ConfigError> {
    // Whole-file syntax errors: not our department. Return the error via
    // ParseOutcome so the caller can warn without our forcing an exit.
    let value: toml::Value = match toml::from_str(raw) {
        Ok(v) => v,
        Err(e) => return Ok(ParseOutcome::WholeFileParseError(e)),
    };
    let Some(oxitest_value) = value.get("tool").and_then(|t| t.get("oxitest")) else {
        return Ok(ParseOutcome::Absent);
    };
    // Deserialize just the subtable — errors here ARE our department.
    let config: OxitestConfig = oxitest_value.clone().try_into()?;
    Ok(ParseOutcome::Present(config))
}

#[derive(serde::Deserialize, Debug, Clone)]
#[serde(untagged)]
pub(super) enum AutoArrangeToml {
    Threshold(u8),
    Disabled(bool),
}

/// Root-level pyproject.toml deserialization target.
///
/// Deliberately lacks `#[serde(deny_unknown_fields)]` — `[project]`,
/// `[build-system]`, and other root tables live here alongside `[tool]`.
/// Adding it would break every pyproject that isn't oxitest-only. See ADR-0008.
#[derive(Deserialize, Default, Debug)]
pub(super) struct PyprojectToml {
    pub(super) tool: Option<ToolTable>,
}

/// The `[tool]` sub-table.
///
/// Deliberately lacks `#[serde(deny_unknown_fields)]` — `[tool.ruff]`,
/// `[tool.mypy]`, `[tool.black]`, and other tools' sub-tables live here
/// alongside `[tool.oxitest]`. Adding it would break every user pyproject
/// that configures any other tool. See ADR-0008.
#[derive(Deserialize, Default, Debug)]
pub(super) struct ToolTable {
    pub(super) oxitest: Option<OxitestConfig>,
}

#[derive(Deserialize, Default, Debug)]
#[serde(deny_unknown_fields)]
pub(super) struct OxitestConfig {
    pub(super) testpaths: Option<Vec<String>>,
    pub(super) python_files: Option<Vec<String>>,
    pub(super) norecursedirs: Option<Vec<String>>,
    pub(super) markers: Option<Vec<String>>,
    pub(super) timeout: Option<u64>,
    pub(super) cache_max_age: Option<u32>,
    pub(super) min_parallel_tests: Option<usize>,
    pub(super) timeout_multiplier: Option<f64>,
    pub(super) spawn_overhead_ms: Option<f64>,
    pub(super) strict: Option<super::StrictMode>,
    pub(super) workers: Option<WorkerCount>,
    pub(super) schedule: Option<super::ScheduleStrategy>,
    pub(super) failed: Option<super::FailedMode>,
    pub(super) tb: Option<super::TbStyle>,
    pub(super) verbosity: Option<super::Verbosity>,
    pub(super) maxfail: Option<usize>,
    pub(super) durations: Option<usize>,
    pub(super) serial: Option<bool>,
    pub(super) color: Option<super::ColorMode>,
    pub(super) plugins: Option<Vec<String>>,
    #[serde(default)]
    pub(super) plugin_settings: std::collections::HashMap<String, toml::Value>,
    pub(super) async_backend: Option<String>,
    pub(super) affected_base: Option<String>,
    pub(super) retries: Option<usize>,
    pub(super) retries_delay: Option<u64>,
    pub(super) keep_tmp: Option<super::KeepTmpMode>,
    pub(super) auto_arrange: Option<AutoArrangeToml>,
    pub(super) show_locals: Option<bool>,
    pub(super) show_internals: Option<bool>,
    pub(super) use_gitignore: Option<bool>,
    pub(super) doctest: Option<DoctestConfig>,
    pub(super) inspect_timeout: Option<u64>,
}

#[derive(Debug, Deserialize, Default, PartialEq, Eq, Clone)]
#[serde(deny_unknown_fields)]
pub struct DoctestConfig {
    pub scope: Option<DoctestScope>,
    /// Entries to exclude from doctest coverage. Same grammar as `scope`
    /// (list form): prefix, file, or `file.py::symbol`. Files/subjects
    /// matched by any skip entry are excluded from both enumeration and
    /// analysis.
    #[serde(default)]
    pub skip: Vec<ScopeEntry>,
}

/// A single entry in `[tool.oxitest.doctest].scope` (list form) or `.skip`.
///
/// Grammar mirrors pytest node IDs. See spec 2026-07-25 for the full rule set.
///
/// Deferred to #1644: `file.py::Cls::method` (member-level scope).
#[derive(Debug, PartialEq, Eq, Clone)]
pub enum ScopeEntry {
    /// Directory prefix, trailing `/` required. Matches subjects under the prefix.
    Prefix(Utf8PathBuf),
    /// Whole `.py` file. Matches every subject in the file.
    File(Utf8PathBuf),
    /// Top-level symbol (`def` or `class`) in a `.py` file.
    Symbol { file: Utf8PathBuf, name: String },
}

impl<'de> serde::Deserialize<'de> for ScopeEntry {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let raw = String::deserialize(deserializer)?;
        parse_scope_entry_str(&raw).map_err(serde::de::Error::custom)
    }
}

/// Classify an entry string into a `ScopeEntry`. Returns a human-readable error
/// with the offending entry quoted on any failure. See spec for the full grammar.
///
/// The `split_node_id_str` helper is non-validating — this function applies the
/// strict-mode policy: empty segments rejected, chain length limited to 1,
/// 2-segment chains rejected with a #1644 pointer.
pub(crate) fn parse_scope_entry_str(raw: &str) -> Result<ScopeEntry, String> {
    // Reject absolute paths and parent components up front — same for all forms.
    // Windows drive-letter form covers both `C:\path` (backslash, native) and
    // `C:/path` (forward slash, which many toolchains normalize into).
    let looks_like_windows_drive =
        raw.chars().nth(1) == Some(':') && matches!(raw.chars().nth(2), Some('\\') | Some('/'));
    if raw.starts_with('/') || raw.starts_with('\\') || looks_like_windows_drive {
        return Err(format!(
            "entry '{raw}' must be a rootdir-relative path, not absolute"
        ));
    }
    for component in raw.split('/') {
        if component == ".." {
            return Err(format!("entry '{raw}' must not contain '..' components"));
        }
    }

    // Split on `::` using the shared node-ID helper.
    let (file_part, chain) = crate::types::node_id::split_node_id_str(raw);

    if chain.is_empty() {
        // No `::` — must be Prefix or File.
        if raw.ends_with('/') {
            return Ok(ScopeEntry::Prefix(Utf8PathBuf::from(raw)));
        }
        if raw.ends_with(".py") {
            return Ok(ScopeEntry::File(Utf8PathBuf::from(raw)));
        }
        return Err(format!(
            "entry '{raw}' is ambiguous — add trailing / for a directory, .py for a file, or ::name for a symbol"
        ));
    }

    // Chain present — validate its segments individually first.
    if chain.iter().any(|s| s.is_empty()) {
        return Err(format!("entry '{raw}' has an empty segment after ::"));
    }

    // File part must be a .py file when chain is present.
    if !file_part.ends_with(".py") {
        return Err(format!(
            "entry '{raw}' — path before :: must end in .py (got '{file_part}')"
        ));
    }
    let file = Utf8PathBuf::from(file_part);

    match chain.len() {
        1 => Ok(ScopeEntry::Symbol {
            file,
            name: chain[0].to_string(),
        }),
        2 => Err(format!(
            "entry '{raw}' — member-level scope not yet supported, see #1644"
        )),
        _ => Err(format!("entry '{raw}' has too many :: segments")),
    }
}

/// `scope` field of `[tool.oxitest.doctest]`. Either the scalar `"public"` or
/// a list of `ScopeEntry` values. `"off"` is intentionally not accepted —
/// drop the whole `[tool.oxitest.doctest]` table to disable coverage.
#[derive(Debug, PartialEq, Eq, Clone)]
pub enum DoctestScope {
    Public,
    List(Vec<ScopeEntry>),
}

impl<'de> serde::Deserialize<'de> for DoctestScope {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct V;
        impl<'de> serde::de::Visitor<'de> for V {
            type Value = DoctestScope;

            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str(r#""public" or a list of entry strings"#)
            }

            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<DoctestScope, E> {
                match v {
                    "public" => Ok(DoctestScope::Public),
                    "off" => Err(E::custom(
                        "scope value 'off' is no longer supported; remove the whole [tool.oxitest.doctest] table to disable coverage",
                    )),
                    other => Err(E::custom(format!(
                        r#"expected "public" or a list, got scope = "{other}""#
                    ))),
                }
            }

            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> Result<DoctestScope, A::Error> {
                let mut entries = Vec::new();
                while let Some(entry) = seq.next_element::<ScopeEntry>()? {
                    entries.push(entry);
                }
                Ok(DoctestScope::List(entries))
            }
        }

        deserializer.deserialize_any(V)
    }
}

impl<'de> serde::Deserialize<'de> for WorkerCount {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct WorkerCountVisitor;

        impl<'de> serde::de::Visitor<'de> for WorkerCountVisitor {
            type Value = WorkerCount;

            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("\"auto\" or a positive integer")
            }

            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<WorkerCount, E> {
                if v.eq_ignore_ascii_case("auto") {
                    Ok(WorkerCount::Auto)
                } else {
                    Err(E::custom(format!("expected \"auto\", got \"{v}\"")))
                }
            }

            fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<WorkerCount, E> {
                let n: usize = v
                    .try_into()
                    .map_err(|_| E::custom("worker count must be at least 1"))?;
                WorkerCount::try_from_count(n).map_err(E::custom)
            }

            fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<WorkerCount, E> {
                let n: usize = v
                    .try_into()
                    .map_err(|_| E::custom("worker count too large"))?;
                WorkerCount::try_from_count(n).map_err(E::custom)
            }
        }

        deserializer.deserialize_any(WorkerCountVisitor)
    }
}

/// Format a `ScopeEntry` back into its TOML source form for use in diagnostics.
///
/// Round-trips: `render_entry(parse_scope_entry_str(s).unwrap())` == `s`.
pub(crate) fn render_entry(e: &ScopeEntry) -> String {
    match e {
        ScopeEntry::Prefix(p) => p.as_str().to_string(),
        ScopeEntry::File(f) => f.as_str().to_string(),
        ScopeEntry::Symbol { file, name } => format!("{}::{}", file.as_str(), name),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Parse a `[tool.oxitest]` section from inline TOML and return `OxitestConfig`.
    fn parse_oxitest(toml: &str) -> OxitestConfig {
        try_parse_oxitest(toml).expect("valid TOML")
    }

    /// Parse a `[tool.oxitest]` section, running the same structural checks
    /// (legacy-key rejection) that `Config::load` uses in production.
    ///
    /// Sibling to `parse_oxitest` — returns `Result` so tests can inspect
    /// migration errors from `check_no_legacy_keys` without every
    /// happy-path caller having to `.unwrap()`.
    fn try_parse_oxitest(toml: &str) -> Result<OxitestConfig, ConfigError> {
        let full = format!("[tool.oxitest]\n{toml}");
        check_no_legacy_keys(&full)?;
        let parsed: PyprojectToml = toml::from_str(&full)?;
        Ok(parsed
            .tool
            .expect("tool table present")
            .oxitest
            .expect("oxitest table present"))
    }

    /// Parse a `[tool.oxitest]` section and expect deserialization to fail.
    fn parse_oxitest_err(toml: &str) -> toml::de::Error {
        let full = format!("[tool.oxitest]\n{toml}");
        toml::from_str::<PyprojectToml>(&full).expect_err("expected parse error")
    }

    // ── WorkerCount deserialization ───────────────────────────────────────────

    #[test]
    fn workers_auto_lowercase() {
        let cfg = parse_oxitest(r#"workers = "auto""#);
        assert_eq!(
            cfg.workers,
            Some(WorkerCount::Auto),
            "\"auto\" should deserialize to WorkerCount::Auto"
        );
    }

    #[test]
    fn workers_auto_mixed_case() {
        let cfg = parse_oxitest(r#"workers = "AUTO""#);
        assert_eq!(
            cfg.workers,
            Some(WorkerCount::Auto),
            "\"AUTO\" should be accepted case-insensitively"
        );
    }

    #[test]
    fn workers_positive_integer() {
        let cfg = parse_oxitest("workers = 4");
        assert_eq!(
            cfg.workers,
            Some(WorkerCount::Fixed(4)),
            "positive integer should deserialize to WorkerCount::Fixed"
        );
    }

    #[test]
    fn workers_invalid_string_rejected() {
        let err = parse_oxitest_err(r#"workers = "bogus""#);
        let msg = err.to_string();
        assert!(
            msg.contains("expected \"auto\"") || msg.contains("auto"),
            "invalid string should report it expected \"auto\", got: {msg}"
        );
    }

    #[test]
    fn workers_zero_rejected() {
        let err = parse_oxitest_err("workers = 0");
        let msg = err.to_string();
        assert!(
            msg.contains("at least 1"),
            "zero workers should be rejected with 'at least 1' message, got: {msg}"
        );
    }

    #[test]
    fn workers_negative_rejected() {
        let err = parse_oxitest_err("workers = -1");
        let msg = err.to_string();
        assert!(
            !msg.is_empty(),
            "negative worker count must be rejected, got empty error"
        );
    }

    // ── OxitestConfig field deserialization ───────────────────────────────────

    #[test]
    fn testpaths_array() {
        let cfg = parse_oxitest(r#"testpaths = ["tests", "integration"]"#);
        assert_eq!(
            cfg.testpaths,
            Some(vec!["tests".to_string(), "integration".to_string()]),
            "testpaths should deserialize as a Vec<String>"
        );
    }

    #[test]
    fn timeout_integer() {
        let cfg = parse_oxitest("timeout = 30");
        assert_eq!(cfg.timeout, Some(30), "timeout should deserialize as u64");
    }

    #[test]
    fn markers_array() {
        let cfg = parse_oxitest(r#"markers = ["slow", "integration"]"#);
        assert_eq!(
            cfg.markers,
            Some(vec!["slow".to_string(), "integration".to_string()]),
            "markers should deserialize as Vec<String>"
        );
    }

    #[test]
    fn plugins_array() {
        let cfg = parse_oxitest(r#"plugins = ["my_plugin", "another"]"#);
        assert_eq!(
            cfg.plugins,
            Some(vec!["my_plugin".to_string(), "another".to_string()]),
            "plugins should deserialize as Vec<String>"
        );
    }

    #[test]
    fn empty_tool_oxitest_table() {
        let cfg = parse_oxitest("");
        assert!(
            cfg.testpaths.is_none(),
            "empty [tool.oxitest] table should leave testpaths as None"
        );
        assert!(
            cfg.workers.is_none(),
            "empty [tool.oxitest] table should leave workers as None"
        );
        assert!(
            cfg.timeout.is_none(),
            "empty [tool.oxitest] table should leave timeout as None"
        );
    }

    #[test]
    fn missing_tool_table_gives_default_pyproject() {
        let parsed: PyprojectToml = toml::from_str("").expect("empty TOML is valid");
        assert!(
            parsed.tool.is_none(),
            "TOML without [tool] section should deserialize to tool = None"
        );
    }

    #[test]
    fn auto_arrange_threshold_integer() {
        let cfg = parse_oxitest("auto_arrange = 80");
        match cfg.auto_arrange {
            Some(AutoArrangeToml::Threshold(n)) => assert_eq!(
                n, 80,
                "auto_arrange integer should deserialize to Threshold(80)"
            ),
            other => panic!("expected Some(Threshold(80)), got {other:?}"),
        }
    }

    #[test]
    fn auto_arrange_disabled_false() {
        let cfg = parse_oxitest("auto_arrange = false");
        match cfg.auto_arrange {
            Some(AutoArrangeToml::Disabled(false)) => {}
            other => panic!("expected Some(Disabled(false)), got {other:?}"),
        }
    }

    #[test]
    fn doctest_sub_table_parses_full_shape() {
        let toml_src = r#"
[tool.oxitest.doctest]
scope = "public"
skip = ["python/tests/fixtures/"]
"#;
        let parsed: PyprojectToml = toml::from_str(toml_src).expect("valid TOML");
        let cfg = parsed
            .tool
            .expect("tool table present")
            .oxitest
            .expect("oxitest table present");
        let dt = cfg.doctest.expect("doctest sub-table present");
        assert_eq!(dt.scope, Some(DoctestScope::Public));
        assert_eq!(
            dt.skip,
            vec![ScopeEntry::Prefix(Utf8PathBuf::from(
                "python/tests/fixtures/"
            ))],
        );
    }

    #[test]
    fn legacy_doctest_modules_key_hard_errors() {
        let err = try_parse_oxitest(
            r#"
doctest_modules = true
"#,
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("doctest_modules"),
            "error should mention the legacy key by name for a helpful message: got {}",
            err
        );
        assert!(
            err.to_string().contains("[tool.oxitest.doctest]"),
            "error should point users at the new sub-table: got {}",
            err
        );
    }

    #[test]
    fn legacy_doctest_modules_false_also_hard_errors() {
        // The rejection is value-agnostic — the key's presence is enough.
        let err = try_parse_oxitest(
            r#"
doctest_modules = false
"#,
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("doctest_modules"),
            "value-agnostic rejection: false should hard-error too, got: {}",
            err
        );
    }

    #[test]
    fn doctest_sub_table_accepts_skip_paths() {
        // Use the full pyproject.toml shape so [tool.oxitest.doctest] is a
        // proper sub-table (not prepended by the parse_oxitest helper).
        let toml_src = r#"
[tool.oxitest.doctest]
scope = "public"
skip = ["python/tests/fixtures/", "python/tests/docs/"]
"#;
        let parsed: PyprojectToml = toml::from_str(toml_src).expect("valid TOML");
        let cfg = parsed
            .tool
            .expect("tool table present")
            .oxitest
            .expect("oxitest table present");
        let dt = cfg.doctest.expect("doctest sub-table present");
        assert_eq!(
            dt.skip,
            vec![
                ScopeEntry::Prefix(Utf8PathBuf::from("python/tests/fixtures/")),
                ScopeEntry::Prefix(Utf8PathBuf::from("python/tests/docs/")),
            ],
            "skip must parse as a list of ScopeEntry values using the shared grammar"
        );
    }

    #[test]
    fn doctest_sub_table_skip_defaults_to_empty() {
        // Omitting `skip` must default to an empty Vec, not None.
        let toml_src = r#"
[tool.oxitest.doctest]
scope = "public"
"#;
        let parsed: PyprojectToml = toml::from_str(toml_src).expect("valid TOML");
        let cfg = parsed
            .tool
            .expect("tool table present")
            .oxitest
            .expect("oxitest table present");
        let dt = cfg.doctest.expect("doctest sub-table present");
        assert!(
            dt.skip.is_empty(),
            "omitted skip defaults to an empty list; got {:?}",
            dt.skip
        );
    }

    #[test]
    fn legacy_key_mixed_with_new_sub_table_still_errors() {
        // Users mid-migration might paste both; the legacy key should still hard-error.
        let err = try_parse_oxitest(
            r#"
doctest_modules = true

[doctest]
scope = "public"
"#,
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("doctest_modules"),
            "legacy key + new sub-table ⇒ still errors on legacy key: got {}",
            err
        );
    }

    // ── Doctest sub-table: invalid values, unknown keys, defaults ─────────────

    #[test]
    fn invalid_scope_enum_hard_fails_at_parse() {
        // Inline-table form keeps the doctest fields nested under
        // `[tool.oxitest]` after the helper's prepend — a bare `[doctest]`
        // header would land at the top level, bypassing DoctestConfig entirely.
        let err = try_parse_oxitest(
            r#"
doctest = { scope = "invalid" }
"#,
        )
        .unwrap_err();
        let msg = err.to_string().to_lowercase();
        assert!(
            msg.contains("scope") || msg.contains("invalid"),
            "TOML parse error should surface the bad enum: got {}",
            err
        );
    }

    #[test]
    fn unknown_keys_in_doctest_sub_table_hard_fail() {
        let err = try_parse_oxitest(
            r#"
doctest = { scope = "public", bogus_key = "x" }
"#,
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("bogus_key"),
            "deny_unknown_fields should name the offender: got {}",
            err
        );
    }

    #[test]
    fn section_present_with_omitted_keys_uses_defaults_at_resolve_time() {
        let cfg = try_parse_oxitest(
            r#"
doctest = {}
"#,
        )
        .unwrap();
        let dt = cfg.doctest.expect("empty sub-table still constructs Some");
        assert_eq!(
            dt.scope, None,
            "raw sub-table stores None; defaults applied by Config::resolve"
        );
        assert!(dt.skip.is_empty(), "skip omitted ⇒ empty at parse time");
    }

    // ── ScopeEntry deserialization ────────────────────────────────────────────

    #[test]
    fn scope_entry_parses_directory_prefix() {
        let raw: std::collections::HashMap<String, ScopeEntry> =
            toml::from_str(r#"e = "src/pkg/""#).unwrap();
        assert_eq!(
            raw["e"],
            ScopeEntry::Prefix(Utf8PathBuf::from("src/pkg/")),
            "trailing / classifies as Prefix",
        );
    }

    #[test]
    fn scope_entry_parses_python_file() {
        let raw: std::collections::HashMap<String, ScopeEntry> =
            toml::from_str(r#"e = "src/mod.py""#).unwrap();
        assert_eq!(
            raw["e"],
            ScopeEntry::File(Utf8PathBuf::from("src/mod.py")),
            ".py suffix (no ::) classifies as File",
        );
    }

    #[test]
    fn scope_entry_parses_top_level_symbol() {
        let raw: std::collections::HashMap<String, ScopeEntry> =
            toml::from_str(r#"e = "src/mod.py::foo""#).unwrap();
        assert_eq!(
            raw["e"],
            ScopeEntry::Symbol {
                file: Utf8PathBuf::from("src/mod.py"),
                name: "foo".to_string(),
            },
            "file.py::sym classifies as Symbol",
        );
    }

    /// Deserialize a single `ScopeEntry` from a TOML string, returning any error verbatim.
    fn parse_scope_entry(s: &str) -> Result<ScopeEntry, String> {
        let src = format!(r#"e = "{s}""#);
        toml::from_str::<std::collections::HashMap<String, ScopeEntry>>(&src)
            .map(|m| m["e"].clone())
            .map_err(|e| e.to_string())
    }

    #[test]
    fn scope_entry_rejects_ambiguous_bare() {
        let err = parse_scope_entry("src/mod").unwrap_err();
        assert!(
            err.contains("ambiguous") && err.contains("src/mod"),
            "bare entry with no suffix/separator must be rejected (got: {err})",
        );
    }

    #[test]
    fn scope_entry_rejects_member_syntax_pointing_at_followup() {
        let err = parse_scope_entry("src/mod.py::Cls::method").unwrap_err();
        assert!(
            err.contains("member-level") && err.contains("#1644"),
            "member syntax must be rejected with a pointer to the follow-up issue (got: {err})",
        );
    }

    #[test]
    fn scope_entry_rejects_too_many_segments() {
        let err = parse_scope_entry("src/mod.py::A::B::C").unwrap_err();
        assert!(
            err.contains("too many"),
            "3+ segments after :: must be rejected (got: {err})",
        );
    }

    #[test]
    fn scope_entry_rejects_non_py_file_before_double_colon() {
        let err = parse_scope_entry("src/not_py::foo").unwrap_err();
        assert!(
            err.contains(".py") && err.contains("src/not_py"),
            "file part before :: must end in .py (got: {err})",
        );
    }

    #[test]
    fn scope_entry_rejects_absolute_path() {
        let err = parse_scope_entry("/abs/mod.py").unwrap_err();
        assert!(
            err.contains("rootdir-relative") || err.contains("absolute"),
            "absolute paths must be rejected (got: {err})",
        );
    }

    #[test]
    fn scope_entry_rejects_windows_absolute_paths() {
        // Bypass TOML wrapping — backslashes trigger TOML string escape rules
        // and would obscure what we're actually asserting about the parser.
        for input in ["C:\\Users\\foo\\mod.py", "C:/Users/foo/mod.py"] {
            let err = parse_scope_entry_str(input).unwrap_err();
            assert!(
                err.contains("rootdir-relative") || err.contains("absolute"),
                "Windows drive-letter absolute paths (both slash forms) must be rejected — the config layer must not silently accept them and then fail path matching later (input: {input}, got: {err})",
            );
        }
    }

    #[test]
    fn scope_entry_rejects_parent_component() {
        let err = parse_scope_entry("../mod.py").unwrap_err();
        assert!(
            err.contains(".."),
            "entries with .. must be rejected (got: {err})",
        );
    }

    #[test]
    fn scope_entry_rejects_empty_symbol_after_double_colon() {
        let err = parse_scope_entry("src/mod.py::").unwrap_err();
        assert!(
            err.contains("empty"),
            "empty segment after :: must be rejected (got: {err})",
        );
    }

    // ── DoctestScope deserialization ──────────────────────────────────────────

    #[test]
    fn doctest_scope_parses_public_scalar() {
        let cfg: DoctestConfig = toml::from_str(r#"scope = "public""#).unwrap();
        assert_eq!(
            cfg.scope,
            Some(DoctestScope::Public),
            "\"public\" scalar → Public variant",
        );
    }

    #[test]
    fn doctest_scope_parses_empty_list_as_empty_list() {
        let cfg: DoctestConfig = toml::from_str(r#"scope = []"#).unwrap();
        assert_eq!(
            cfg.scope,
            Some(DoctestScope::List(vec![])),
            "empty array → List with zero entries (rule fires but matches nothing)",
        );
    }

    #[test]
    fn doctest_scope_parses_heterogeneous_list() {
        let cfg: DoctestConfig =
            toml::from_str(r#"scope = ["src/pkg/", "src/mod.py", "src/mod.py::foo"]"#).unwrap();
        let DoctestScope::List(entries) = cfg.scope.unwrap() else {
            panic!("expected DoctestScope::List for TOML array");
        };
        assert_eq!(
            entries,
            vec![
                ScopeEntry::Prefix(Utf8PathBuf::from("src/pkg/")),
                ScopeEntry::File(Utf8PathBuf::from("src/mod.py")),
                ScopeEntry::Symbol {
                    file: Utf8PathBuf::from("src/mod.py"),
                    name: "foo".to_string(),
                },
            ],
            "heterogeneous list preserves entry order and mixes variants",
        );
    }

    #[test]
    fn doctest_scope_rejects_off_with_migration_hint() {
        let err = toml::from_str::<DoctestConfig>(r#"scope = "off""#)
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("off") && err.contains("[tool.oxitest.doctest]"),
            "scope = \"off\" must be rejected with a migration hint pointing at the whole doctest table (got: {err})",
        );
    }

    #[test]
    fn doctest_scope_rejects_unknown_scalar() {
        let err = toml::from_str::<DoctestConfig>(r#"scope = "private""#)
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("private"),
            "unknown scalar must be rejected with the offending value in the error (got: {err})",
        );
    }

    #[test]
    fn doctest_skip_parses_list_of_scope_entries() {
        let cfg: DoctestConfig = toml::from_str(
            r#"skip = ["tests/", "src/generated.py", "src/mod.py::internal_helper"]"#,
        )
        .unwrap();
        assert_eq!(
            cfg.skip,
            vec![
                ScopeEntry::Prefix(Utf8PathBuf::from("tests/")),
                ScopeEntry::File(Utf8PathBuf::from("src/generated.py")),
                ScopeEntry::Symbol {
                    file: Utf8PathBuf::from("src/mod.py"),
                    name: "internal_helper".to_string(),
                },
            ],
            "skip uses the same grammar as scope list — heterogeneous entries preserved",
        );
    }

    #[test]
    fn doctest_skip_rejects_ambiguous_entry() {
        let err = toml::from_str::<DoctestConfig>(r#"skip = ["tests"]"#)
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("ambiguous") && err.contains("tests"),
            "skip entries follow the same strict grammar (got: {err})",
        );
    }

    #[test]
    fn render_entry_round_trips_all_three_forms() {
        for input in ["src/pkg/", "src/mod.py", "src/mod.py::foo"] {
            let entry = parse_scope_entry_str(input).unwrap();
            assert_eq!(
                render_entry(&entry),
                input,
                "render_entry must round-trip parse_scope_entry_str for '{input}' so diagnostics quote the exact TOML form the user wrote",
            );
        }
    }

    #[test]
    fn parse_present_for_well_formed_oxitest_config() {
        let raw = r#"
[tool.oxitest]
timeout = 60
"#;
        let outcome = parse_oxitest_config(raw).expect(
            "well-formed config must not Err — otherwise the extractor is \
                     rejecting valid input",
        );
        let ParseOutcome::Present(cfg) = outcome else {
            panic!("well-formed [tool.oxitest] must produce Present variant, got: {outcome:?}",);
        };
        assert_eq!(cfg.timeout, Some(60), "timeout field must round-trip");
    }

    #[test]
    fn parse_absent_for_missing_oxitest_subtable() {
        let raw = r#"
[project]
name = "foo"

[tool.ruff]
line-length = 100
"#;
        let outcome = parse_oxitest_config(raw).expect(
            "valid TOML without [tool.oxitest] must not Err — other tools' \
                     configs are not our concern",
        );
        assert!(
            matches!(outcome, ParseOutcome::Absent),
            "missing [tool.oxitest] must produce Absent, got: {outcome:?}",
        );
    }

    #[test]
    fn parse_whole_file_error_for_broken_toml() {
        // Broken bracket in [tool.ruff] — syntactically invalid whole-file TOML.
        let raw = r#"
[tool.ruff
line-length = 100
"#;
        let outcome = parse_oxitest_config(raw).expect(
            "whole-file TOML syntax errors must not hard-fail oxitest — \
                     narrow scope per ADR-0008",
        );
        assert!(
            matches!(outcome, ParseOutcome::WholeFileParseError(_)),
            "broken whole-file TOML must produce WholeFileParseError, got: {outcome:?}",
        );
    }

    #[test]
    fn parse_errs_on_unknown_field_in_oxitest_subtable() {
        let raw = r#"
[tool.oxitest]
waivres = "typo"
"#;
        let err = parse_oxitest_config(raw).expect_err(
            "unknown fields in [tool.oxitest] must Err — silent drop would let \
             typos evade the fail-closed contract per ADR-0008",
        );
        let msg = err.to_string();
        assert!(
            msg.contains("waivres"),
            "the error must name the offending field so users can grep for it in \
             pyproject.toml — got: {msg}",
        );
    }

    #[test]
    fn parse_errs_on_wrong_type_in_oxitest_subtable() {
        let raw = r#"
[tool.oxitest]
timeout = "not-a-number"
"#;
        let err = parse_oxitest_config(raw).expect_err(
            "type mismatches in [tool.oxitest] must Err — silent coercion or \
             default-substitution would hide config bugs per ADR-0008",
        );
        let msg = err.to_string();
        assert!(
            msg.contains("timeout"),
            "the error must name the offending field — got: {msg}",
        );
    }

    #[test]
    fn parse_present_for_empty_oxitest_subtable() {
        let raw = r#"
[tool.oxitest]
"#;
        let outcome = parse_oxitest_config(raw)
            .expect("empty [tool.oxitest] must not Err — the table exists, just no keys");
        let ParseOutcome::Present(cfg) = outcome else {
            panic!("empty [tool.oxitest] must produce Present with defaults, got: {outcome:?}");
        };
        assert_eq!(
            cfg.timeout, None,
            "unspecified fields must remain None so downstream layers apply defaults \
             (not silently zeroed)",
        );
    }
}
