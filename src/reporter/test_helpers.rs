//! Shared test helpers for reporter unit tests.
#![cfg(test)]

use camino::Utf8PathBuf;

use crate::types::{NodeId, TestItem, TestOutcome};

/// Build a `TestItem` whose `node_id` is constructed via `NodeId::new` using
/// the canonical test module path `"tests/test_foo.py"`.
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

/// Build a `TestItem` from an already-formatted `node_id` string (e.g.
/// `"tests/test_foo.py::test_fn"`).  Used by modules that receive raw node
/// IDs from workers or other external sources.
pub(crate) fn make_item_raw(node_id: &str) -> TestItem {
    TestItem {
        node_id: NodeId::from_raw(node_id),
        module_path: Utf8PathBuf::from("tests/test_foo.py"),
        fn_name: node_id.to_string(),
        lineno: 0,
        markers: vec![],
        param_id: None,
        param_values: vec![],
    }
}

/// Build a `TestItem` with an explicit `module` path, constructing the
/// `node_id` via `NodeId::new(module, name, None)`.
pub(crate) fn make_item_in(name: &str, module: &str) -> TestItem {
    TestItem {
        node_id: NodeId::new(module, name, None),
        module_path: Utf8PathBuf::from(module),
        fn_name: name.to_string(),
        lineno: 0,
        markers: vec![],
        param_id: None,
        param_values: vec![],
    }
}

/// Build a `(Utf8PathBuf, Vec<TestItem>)` group for `module`, one item per
/// name in `names`.
pub(crate) fn make_group(module: &str, names: &[&str]) -> (Utf8PathBuf, Vec<TestItem>) {
    let path = Utf8PathBuf::from(module);
    let items = names
        .iter()
        .map(|name| TestItem {
            node_id: NodeId::new(module, name, None),
            module_path: path.clone(),
            fn_name: name.to_string(),
            lineno: 0,
            markers: vec![],
            param_id: None,
            param_values: vec![],
        })
        .collect();
    (path, items)
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
        frames: vec![],
    }
}

pub(crate) fn make_error(msg: &str, file: &str, lineno: usize, src: &str) -> TestOutcome {
    TestOutcome::Error {
        message: msg.to_string(),
        file: file.to_string(),
        lineno,
        source_line: src.to_string(),
        frames: vec![],
    }
}
