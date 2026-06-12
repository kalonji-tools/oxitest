mod ahash_tests {
    #[test]
    fn ahash_map_is_available() {
        let mut m: ahash::AHashMap<String, usize> = ahash::AHashMap::new();
        m.insert("key".to_string(), 42);
        assert_eq!(m.get("key"), Some(&42));
    }
}

mod channel_tests {
    #[test]
    fn crossbeam_channel_drains_when_all_senders_dropped() {
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
        tracing::warn!("no-op warning");
        tracing::error!("no-op error");
    }

    #[test]
    fn tracing_structured_fields_compile() {
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

mod strict_pipeline_tests {
    use crate::bridge;
    use crate::config::Config;
    use crate::strict::{self, PerTestViolation, StrictViolation};
    use crate::types::{NodeId, TestItem};

    #[test]
    fn all_violations_empty_when_strict_none() {
        let cfg = Config::default();
        let raw: Vec<bridge::RawViolation> = vec![];
        let violations: Vec<StrictViolation> = if cfg.markers.strict.is_some() {
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
            TestItem::builder_raw("tests/test_foo.py::test_bad").arc(),
            TestItem::builder_raw("tests/test_foo.py::test_good").arc(),
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
