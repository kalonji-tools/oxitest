use super::helpers;
use super::*;

mod mtime_tests {
    use super::*;

    #[test]
    fn file_mtime_secs_returns_nonzero_for_existing_file() {
        let mtime = helpers::file_mtime_secs(camino::Utf8Path::new(file!()));
        assert!(mtime > 0, "mtime must be non-zero for an existing file");
    }

    #[test]
    fn file_mtime_secs_returns_zero_for_missing_file() {
        let mtime = helpers::file_mtime_secs(camino::Utf8Path::new("/nonexistent/path/xyz.py"));
        assert_eq!(mtime, 0);
    }
}

mod timeout_tests {
    use super::*;
    use crate::cache::TestCache;
    use crate::reporter::test_helpers::make_item_raw as make_item;

    #[test]
    fn no_multiplier_returns_global() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(
            helpers::resolve_timeout(&cache, &item, Some(30), None),
            Some(30)
        );
    }

    #[test]
    fn no_multiplier_no_global_returns_none() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(helpers::resolve_timeout(&cache, &item, None, None), None);
    }

    #[test]
    fn multiplier_cold_cache_falls_back_to_global() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent")); // No cached entry → falls back to global
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(
            helpers::resolve_timeout(&cache, &item, Some(30), Some(3.0)),
            Some(30)
        );
    }

    #[test]
    fn multiplier_with_no_global_and_no_cache_returns_none() {
        let cache = TestCache::load(camino::Utf8Path::new("/nonexistent"));
        let item = make_item("tests/test_foo.py::test_a");
        assert_eq!(
            helpers::resolve_timeout(&cache, &item, None, Some(3.0)),
            None
        );
    }
}

mod ahash_tests {
    #[test]
    fn ahash_map_is_available() {
        // Fails to compile without the ahash dep.
        let mut m: ahash::AHashMap<String, usize> = ahash::AHashMap::new();
        m.insert("key".to_string(), 42);
        assert_eq!(m.get("key"), Some(&42));
    }
}

mod channel_tests {
    #[test]
    fn crossbeam_channel_drains_when_all_senders_dropped() {
        // Fails to compile without crossbeam-channel dep.
        let (tx, rx) = crossbeam_channel::unbounded::<u32>();
        let tx2 = tx.clone();
        tx.send(1).unwrap();
        tx2.send(2).unwrap();
        drop(tx);
        drop(tx2);
        let results: Vec<u32> = rx.into_iter().collect();
        assert_eq!(results.len(), 2);
    }
}

mod tracing_tests {
    #[test]
    fn tracing_macros_compile_without_subscriber() {
        // tracing macros are no-ops when no subscriber is active.
        // This test fails to compile without the tracing dep.
        tracing::warn!("no-op warning");
        tracing::error!("no-op error");
    }

    #[test]
    fn tracing_structured_fields_compile() {
        // Verify the structured field syntax used in parallel::spawn_worker compiles.
        let e = serde_json::from_str::<serde_json::Value>("bad").unwrap_err();
        let trimmed = "some output";
        tracing::warn!(error = %e, output = %trimmed, "bad worker output");
    }
}

mod color_tests {
    use crate::config::ColorMode;

    #[test]
    fn always_enables_console_colors() {
        console::set_colors_enabled(false);
        let result = ColorMode::Always.resolve(false);
        assert!(result);
        assert!(console::colors_enabled());
        console::set_colors_enabled(false);
    }

    #[test]
    fn never_returns_false() {
        assert!(!ColorMode::Never.resolve(true));
    }

    #[test]
    fn auto_returns_false_when_not_tty() {
        assert!(!ColorMode::Auto.resolve(false));
    }
}

mod list_tests {
    use super::*;
    use crate::config::Verbosity;
    use crate::types::{LineNo, NodeId, TestItem};
    use camino::Utf8PathBuf;
    use std::sync::Arc;

    fn make_item(
        module: &str,
        fn_name: &str,
        markers: &[&str],
        is_async: bool,
        fixture_names: &[&str],
    ) -> Arc<TestItem> {
        Arc::new(
            TestItem::builder(module, fn_name)
                .lineno(1)
                .markers(markers.iter().map(|s| s.to_string()).collect())
                .async_fn(is_async)
                .fixture_names(fixture_names.iter().map(|s| s.to_string()).collect())
                .build(),
        )
    }

    fn make_param_item(
        module: &str,
        fn_name: &str,
        param_id: &str,
        param_values: &[(&str, &str)],
        markers: &[&str],
        fixture_names: &[&str],
    ) -> Arc<TestItem> {
        let node_id = format!("{module}::{fn_name}[{param_id}]");
        Arc::new(TestItem {
            node_id: NodeId::from_raw(&node_id),
            module_path: Utf8PathBuf::from(module),
            fn_name: fn_name.to_string(),
            lineno: LineNo::new(1),
            markers: markers.iter().map(|s| s.to_string()).collect(),
            param_id: Some(param_id.to_string()),
            param_values: param_values
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            is_async: false,
            fixture_names: fixture_names.iter().map(|s| s.to_string()).collect(),
        })
    }

    #[test]
    fn test_list_empty() {
        let result = helpers::format_test_list(&[], Verbosity::Normal);
        assert_eq!(result, "no tests collected");
    }

    #[test]
    fn test_list_empty_detailed() {
        let result = helpers::format_test_list(&[], Verbosity::Detailed);
        assert_eq!(result, "no tests collected");
    }

    #[test]
    fn test_list_empty_full() {
        let result = helpers::format_test_list(&[], Verbosity::Full);
        assert_eq!(result, "no tests collected");
    }

    #[test]
    fn test_list_normal() {
        let items = vec![
            make_item("tests/test_a.py", "test_one", &[], false, &[]),
            make_item("tests/test_b.py", "test_two", &["slow"], true, &["db"]),
        ];
        insta::assert_snapshot!(helpers::format_test_list(&items, Verbosity::Normal));
    }

    #[test]
    fn test_list_detailed() {
        let items = vec![
            make_item("tests/test_a.py", "test_one", &[], false, &[]),
            make_item(
                "tests/test_a.py",
                "test_two",
                &["slow", "network"],
                true,
                &["db", "tmp"],
            ),
        ];
        insta::assert_snapshot!(helpers::format_test_list(&items, Verbosity::Detailed));
    }

    #[test]
    fn test_list_detailed_no_marks_no_fixtures() {
        let items = vec![make_item("tests/test_a.py", "test_plain", &[], false, &[])];
        insta::assert_snapshot!(helpers::format_test_list(&items, Verbosity::Detailed));
    }

    #[test]
    fn test_list_full_parametrize() {
        let items = vec![
            make_param_item(
                "tests/test_math.py",
                "test_add",
                "basic",
                &[("x", "1"), ("y", "2"), ("expected", "3")],
                &["slow"],
                &["db", "tmp"],
            ),
            make_param_item(
                "tests/test_math.py",
                "test_add",
                "negative",
                &[("x", "-5"), ("y", "3"), ("expected", "-2")],
                &["slow"],
                &["db", "tmp"],
            ),
        ];
        insta::assert_snapshot!(helpers::format_test_list(&items, Verbosity::Full));
    }

    #[test]
    fn test_list_full_mixed() {
        let items = vec![
            make_item("tests/test_a.py", "test_simple", &[], false, &["tmp"]),
            make_param_item(
                "tests/test_a.py",
                "test_calc",
                "pos",
                &[("n", "1")],
                &["math"],
                &[],
            ),
            make_param_item(
                "tests/test_a.py",
                "test_calc",
                "neg",
                &[("n", "-1")],
                &["math"],
                &[],
            ),
        ];
        insta::assert_snapshot!(helpers::format_test_list(&items, Verbosity::Full));
    }

    #[test]
    fn test_list_single_test_singular() {
        let items = vec![make_item("tests/test_a.py", "test_one", &[], false, &[])];
        let result = helpers::format_test_list(&items, Verbosity::Detailed);
        assert!(result.contains("1 test"));
        assert!(!result.contains("1 tests"));
    }
}

mod strict_pipeline_tests {
    use super::*;
    use crate::config::Config;
    use crate::reporter::test_helpers::make_item_raw as make_item;
    use crate::strict::{PerTestViolation, StrictViolation};
    use crate::types::NodeId;

    #[test]
    fn all_violations_empty_when_strict_none() {
        let cfg = Config::default(); // strict = None
        let raw: Vec<bridge::RawViolation> = vec![];
        let violations: Vec<StrictViolation> = if cfg.strict.is_some() {
            let mut v = strict::check_config(&cfg);
            v.extend(strict::check_collected(raw));
            v
        } else {
            vec![]
        };
        assert!(violations.is_empty());
    }

    #[test]
    fn partition_sends_violated_item_to_violated_vec() {
        let items = vec![
            make_item("tests/test_foo.py::test_bad"),
            make_item("tests/test_foo.py::test_good"),
        ];
        let violations = [StrictViolation::PerTest(PerTestViolation::BareAssert {
            node_id: NodeId::from_raw("tests/test_foo.py::test_bad"),
            lines: vec![5],
        })];
        let violated_ids: std::collections::HashSet<&str> = violations
            .iter()
            .filter_map(|v| v.node_id())
            .map(|id| id.as_ref())
            .collect();
        let (violated, clean): (Vec<_>, Vec<_>) = items
            .into_iter()
            .partition(|i| violated_ids.contains(i.node_id.as_ref()));
        assert_eq!(violated.len(), 1);
        assert_eq!(clean.len(), 1);
        assert!(violated[0].node_id.as_ref().contains("test_bad"));
        assert!(clean[0].node_id.as_ref().contains("test_good"));
    }
}
