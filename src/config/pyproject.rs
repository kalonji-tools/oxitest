use super::WorkerCount;
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

#[derive(serde::Deserialize, Debug, Clone)]
#[serde(untagged)]
pub(super) enum AutoArrangeToml {
    Threshold(u8),
    Disabled(bool),
}

#[derive(Deserialize, Default, Debug)]
pub(super) struct PyprojectToml {
    pub(super) tool: Option<ToolTable>,
}

#[derive(Deserialize, Default, Debug)]
pub(super) struct ToolTable {
    pub(super) oxitest: Option<OxitestConfig>,
}

#[derive(Deserialize, Default, Debug)]
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
    /// Path prefixes (rootdir-relative) to exclude from doctest coverage
    /// scanning. Files under any listed prefix are skipped for both subject
    /// enumeration and alias-walking, so no coverage or analysis diagnostics
    /// fire for them.
    #[serde(default)]
    pub skip: Vec<String>,
}

#[derive(Debug, Deserialize, PartialEq, Eq, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub enum DoctestScope {
    Public,
    Off,
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
skip = ["python/tests/fixtures"]
"#;
        let parsed: PyprojectToml = toml::from_str(toml_src).expect("valid TOML");
        let cfg = parsed
            .tool
            .expect("tool table present")
            .oxitest
            .expect("oxitest table present");
        let dt = cfg.doctest.expect("doctest sub-table present");
        assert_eq!(dt.scope, Some(DoctestScope::Public));
        assert_eq!(dt.skip, vec!["python/tests/fixtures".to_owned()]);
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
skip = ["python/tests/fixtures", "python/tests/docs"]
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
                "python/tests/fixtures".to_owned(),
                "python/tests/docs".to_owned()
            ],
            "skip must parse as a list of path prefix strings"
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
}
