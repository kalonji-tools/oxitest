//! Shared test helpers for reporter unit tests.
#![cfg(test)]

use camino::Utf8PathBuf;

use crate::types::{NodeId, TestItem, TestOutcome};

pub(crate) fn make_item(name: &str) -> TestItem {
    TestItem {
        node_id: NodeId::new("tests/test_foo.py", name, None),
        module_path: Utf8PathBuf::from("tests/test_foo.py"),
        fn_name: name.to_string(),
        lineno: 0,
        markers: vec![],
        param_id: None,
        param_values: vec![],
    }
}

pub(crate) fn make_failed(msg: &str, file: &str, lineno: usize, src: &str) -> TestOutcome {
    TestOutcome::Failed {
        message: msg.to_string(),
        file: file.to_string(),
        lineno,
        source_line: src.to_string(),
        left: String::new(),
        right: String::new(),
        op: String::new(),
    }
}

pub(crate) fn make_error(msg: &str, file: &str, lineno: usize, src: &str) -> TestOutcome {
    TestOutcome::Error {
        message: msg.to_string(),
        file: file.to_string(),
        lineno,
        source_line: src.to_string(),
    }
}
