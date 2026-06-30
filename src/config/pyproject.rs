use super::WorkerCount;
use serde::Deserialize;

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
    pub(super) doctest_modules: Option<bool>,
    pub(super) inspect_timeout: Option<u64>,
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
        let full = format!("[tool.oxitest]\n{toml}");
        let parsed: PyprojectToml = toml::from_str(&full).expect("valid TOML");
        parsed
            .tool
            .expect("tool table present")
            .oxitest
            .expect("oxitest table present")
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
}
