#![cfg(test)]
//! Test-only builder helpers for [`TestItem`] and [`TestOutcome`].
//!
//! Extracted from `types/mod.rs` so the domain model is free of test scaffolding.

use super::*;

/// Builder for [`TestItem`], used exclusively in tests.
///
/// Required fields (`module_path`, `fn_name`) are set at construction.
/// All other fields default to empty/zero/false.
pub(crate) struct TestItemBuilder {
    pub(super) node_id: Option<NodeId>,
    pub(super) module_path: Utf8PathBuf,
    pub(super) fn_name: String,
    pub(super) lineno: LineNo,
    pub(super) markers: Vec<String>,
    pub(super) param_id: Option<String>,
    pub(super) param_values: Vec<ParamPair>,
    pub(super) is_async: bool,
    pub(super) fixture_names: Vec<String>,
    pub(super) fixref_names: Vec<String>,
}

impl TestItemBuilder {
    pub(crate) fn lineno(mut self, n: usize) -> Self {
        self.lineno = LineNo::new(n);
        self
    }

    pub(crate) fn markers(mut self, m: Vec<String>) -> Self {
        self.markers = m;
        self
    }

    pub(crate) fn param_id(mut self, id: String) -> Self {
        self.param_id = Some(id);
        self
    }

    #[allow(dead_code)]
    pub(crate) fn param_values(mut self, pv: Vec<ParamPair>) -> Self {
        self.param_values = pv;
        self
    }

    pub(crate) fn async_fn(mut self, val: bool) -> Self {
        self.is_async = val;
        self
    }

    pub(crate) fn fixture_names(mut self, names: Vec<String>) -> Self {
        self.fixture_names = names;
        self
    }

    #[allow(dead_code)]
    pub(crate) fn fixref_names(mut self, names: Vec<String>) -> Self {
        self.fixref_names = names;
        self
    }

    #[allow(dead_code)]
    pub(crate) fn module_path(mut self, path: &str) -> Self {
        self.module_path = Utf8PathBuf::from(path);
        self
    }

    pub(crate) fn build(self) -> TestItem {
        let node_id = self.node_id.unwrap_or_else(|| {
            NodeId::new(
                self.module_path.as_str(),
                &self.fn_name,
                self.param_id.as_deref(),
            )
        });
        TestItem {
            node_id,
            module_path: self.module_path,
            fn_name: self.fn_name,
            lineno: self.lineno,
            markers: self.markers,
            param_id: self.param_id,
            param_values: self.param_values,
            is_async: self.is_async,
            fixture_names: self.fixture_names,
            fixref_names: self.fixref_names,
        }
    }

    pub(crate) fn arc(self) -> std::sync::Arc<TestItem> {
        std::sync::Arc::new(self.build())
    }
}

/// Builder for [`TestOutcome::Failed`], used exclusively in tests.
///
/// Created via [`TestOutcome::failed(msg)`]. All fields default to sensible
/// test values (file = `"tests/test_foo.py"`, lineno = 1, everything else empty).
#[allow(dead_code)]
pub(crate) struct FailedOutcomeBuilder {
    pub(super) message: String,
    pub(super) file: Utf8PathBuf,
    pub(super) lineno: LineNo,
    pub(super) source_line: String,
    pub(super) left: String,
    pub(super) right: String,
    pub(super) op: String,
    pub(super) frames: Vec<Frame>,
    pub(super) field_diffs: Vec<FieldDiff>,
}

#[allow(dead_code)]
impl FailedOutcomeBuilder {
    pub(crate) fn file(mut self, f: &str) -> Self {
        self.file = Utf8PathBuf::from(f);
        self
    }
    pub(crate) fn lineno(mut self, n: usize) -> Self {
        self.lineno = LineNo::new(n);
        self
    }
    pub(crate) fn source(mut self, s: &str) -> Self {
        self.source_line = s.to_string();
        self
    }
    pub(crate) fn comparison(mut self, left: &str, op: &str, right: &str) -> Self {
        self.left = left.to_string();
        self.op = op.to_string();
        self.right = right.to_string();
        self
    }
    pub(crate) fn left(mut self, l: &str) -> Self {
        self.left = l.to_string();
        self
    }
    #[allow(dead_code)]
    pub(crate) fn right(mut self, r: &str) -> Self {
        self.right = r.to_string();
        self
    }
    #[allow(dead_code)]
    pub(crate) fn op(mut self, o: &str) -> Self {
        self.op = o.to_string();
        self
    }
    pub(crate) fn frames(mut self, f: Vec<Frame>) -> Self {
        self.frames = f;
        self
    }
    #[allow(dead_code)]
    pub(crate) fn field_diffs(mut self, d: Vec<FieldDiff>) -> Self {
        self.field_diffs = d;
        self
    }
    pub(crate) fn build(self) -> TestOutcome {
        TestOutcome::Failed {
            message: self.message,
            file: self.file,
            lineno: self.lineno,
            source_line: self.source_line,
            left: self.left,
            right: self.right,
            op: self.op,
            frames: self.frames,
            field_diffs: self.field_diffs,
        }
    }
}

/// Builder for [`TestOutcome::Error`], used exclusively in tests.
///
/// Created via [`TestOutcome::error(msg)`]. All fields default to sensible
/// test values (file = `"tests/test_foo.py"`, lineno = 1, everything else empty).
#[allow(dead_code)]
pub(crate) struct ErrorOutcomeBuilder {
    pub(super) message: String,
    pub(super) file: Utf8PathBuf,
    pub(super) lineno: LineNo,
    pub(super) source_line: String,
    pub(super) frames: Vec<Frame>,
}

#[allow(dead_code)]
impl ErrorOutcomeBuilder {
    pub(crate) fn file(mut self, f: &str) -> Self {
        self.file = Utf8PathBuf::from(f);
        self
    }
    pub(crate) fn lineno(mut self, n: usize) -> Self {
        self.lineno = LineNo::new(n);
        self
    }
    pub(crate) fn source(mut self, s: &str) -> Self {
        self.source_line = s.to_string();
        self
    }
    pub(crate) fn frames(mut self, f: Vec<Frame>) -> Self {
        self.frames = f;
        self
    }
    pub(crate) fn build(self) -> TestOutcome {
        TestOutcome::Error {
            message: self.message,
            file: self.file,
            lineno: self.lineno,
            source_line: self.source_line,
            frames: self.frames,
        }
    }
}
