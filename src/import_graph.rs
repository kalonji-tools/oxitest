//! Import graph for mapping source file changes to affected test files.
//!
//! Built during collection by recording which modules each test file imports.
//! Used by watch mode to selectively re-run only affected tests.

use std::collections::HashSet;

use ahash::AHashMap;
use camino::{Utf8Path, Utf8PathBuf};

/// Maps source files to the test files that depend on them.
#[derive(Debug, Default)]
pub(crate) struct ImportGraph {
    /// source_path → set of test file paths that import from it
    deps: AHashMap<Utf8PathBuf, HashSet<Utf8PathBuf>>,
}

impl ImportGraph {
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Record that `test_file` imports from `source_file`.
    #[allow(dead_code)] // used by tests; production wiring pending import-graph population
    pub(crate) fn add_edge(&mut self, source_file: Utf8PathBuf, test_file: Utf8PathBuf) {
        self.deps.entry(source_file).or_default().insert(test_file);
    }

    /// Given a set of changed files, return which test files need re-running.
    ///
    /// - Changed test file → include directly
    /// - Changed source file with known dependents → include those test files
    /// - Unknown file → reported in `unknown_files` for caller to decide fallback
    pub(crate) fn affected_test_files(
        &self,
        changed: &[Utf8PathBuf],
        test_files: &[Utf8PathBuf],
    ) -> AffectedResult {
        let test_set: HashSet<&Utf8Path> = test_files.iter().map(|p| p.as_path()).collect();
        let mut affected = HashSet::new();
        let mut unknown = Vec::new();

        for path in changed {
            if test_set.contains(path.as_path()) {
                affected.insert(path.clone());
            } else if let Some(dependents) = self.deps.get(path) {
                affected.extend(dependents.iter().cloned());
            } else {
                unknown.push(path.clone());
            }
        }

        AffectedResult {
            test_files: affected.into_iter().collect(),
            unknown_files: unknown,
        }
    }

    /// Remove all edges for a given test file (used when re-collecting).
    #[allow(dead_code)] // used by tests; production wiring pending import-graph population
    pub(crate) fn remove_test_file(&mut self, test_file: &Utf8Path) {
        for dependents in self.deps.values_mut() {
            dependents.remove(test_file);
        }
    }

    /// Check if a path is a conftest file.
    pub(crate) fn is_conftest(path: &Utf8Path) -> bool {
        path.file_name() == Some("conftest.py")
    }
}

/// Result of computing affected test files from a set of changes.
#[derive(Debug)]
pub(crate) struct AffectedResult {
    pub test_files: Vec<Utf8PathBuf>,
    pub unknown_files: Vec<Utf8PathBuf>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_graph_returns_no_affected() {
        let graph = ImportGraph::new();
        let result = graph.affected_test_files(&[], &[]);
        assert!(result.test_files.is_empty());
        assert!(result.unknown_files.is_empty());
    }

    #[test]
    fn changed_test_file_is_directly_affected() {
        let graph = ImportGraph::new();
        let test_file = Utf8PathBuf::from("tests/test_foo.py");
        let result = graph.affected_test_files(&[test_file.clone()], &[test_file.clone()]);
        assert_eq!(result.test_files, vec![test_file]);
    }

    #[test]
    fn changed_source_maps_to_dependent_tests() {
        let mut graph = ImportGraph::new();
        let source = Utf8PathBuf::from("src/auth.py");
        let test_a = Utf8PathBuf::from("tests/test_auth.py");
        let test_b = Utf8PathBuf::from("tests/test_login.py");
        graph.add_edge(source.clone(), test_a.clone());
        graph.add_edge(source.clone(), test_b.clone());

        let result = graph.affected_test_files(&[source], &[test_a.clone(), test_b.clone()]);
        assert_eq!(result.test_files.len(), 2);
        assert!(result.test_files.contains(&test_a));
        assert!(result.test_files.contains(&test_b));
    }

    #[test]
    fn unknown_file_reported_separately() {
        let graph = ImportGraph::new();
        let unknown = Utf8PathBuf::from("src/unknown.py");
        let result = graph.affected_test_files(
            &[unknown.clone()],
            &[Utf8PathBuf::from("tests/test_foo.py")],
        );
        assert!(result.test_files.is_empty());
        assert_eq!(result.unknown_files, vec![unknown]);
    }

    #[test]
    fn remove_test_file_clears_edges() {
        let mut graph = ImportGraph::new();
        let source = Utf8PathBuf::from("src/auth.py");
        let test = Utf8PathBuf::from("tests/test_auth.py");
        graph.add_edge(source.clone(), test.clone());
        graph.remove_test_file(&test);

        let result = graph.affected_test_files(&[source], &[test]);
        assert!(result.test_files.is_empty());
    }

    #[test]
    fn is_conftest_detects_conftest_files() {
        assert!(ImportGraph::is_conftest(Utf8Path::new("tests/conftest.py")));
        assert!(ImportGraph::is_conftest(Utf8Path::new("conftest.py")));
        assert!(!ImportGraph::is_conftest(Utf8Path::new(
            "tests/test_foo.py"
        )));
    }
}
