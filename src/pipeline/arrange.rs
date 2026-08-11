//! Pure partition functions for auto-arrangement of test groups.
//!
//! These functions have zero PyO3 dependencies — they operate only on
//! in-memory `TestItem` collections and are fully testable in isolation.

use std::sync::Arc;

use crate::scheduler::ModuleGroup;
use crate::types::TestItem;

/// Result of splitting module groups into main-process and parallel-eligible sets.
pub(super) struct InprocessPartition {
    /// Tests marked `@oxi.mark.inprocess` — always run on the main process.
    pub inprocess: Vec<ModuleGroup>,
    /// Remaining tests eligible for parallel dispatch.
    pub parallel: Vec<ModuleGroup>,
}

/// Partition module groups into inprocess (main-process) and parallel-eligible groups.
///
/// Tests marked `@oxi.mark.inprocess` are extracted into their own group list.
/// If a module has a mix of inprocess and non-inprocess tests, the module appears
/// in both lists with the appropriate subset.
pub(super) fn partition_inprocess_groups(groups: Vec<ModuleGroup>) -> InprocessPartition {
    let mut inprocess = Vec::new();
    let mut parallel = Vec::new();

    for ModuleGroup { module_path, items } in groups {
        let (inp, par): (Vec<_>, Vec<_>) = items
            .into_iter()
            .partition(|item| item.markers.has_inprocess());

        if !inp.is_empty() {
            inprocess.push(ModuleGroup::new(module_path.clone(), inp));
        }
        if !par.is_empty() {
            parallel.push(ModuleGroup::new(module_path, par));
        }
    }

    InprocessPartition {
        inprocess,
        parallel,
    }
}

/// Result of splitting module groups by shared fixture affinity.
pub(super) struct FixturePartition {
    /// Groups co-located by shared fixture connected component.
    pub arranged: Vec<Vec<ModuleGroup>>,
    /// Groups with no shared fixture dependency.
    pub remaining: Vec<ModuleGroup>,
}

/// Partition test groups by shared fixture affinity.
///
/// Tests whose `fixture_deps` qualifiers overlap with any connected component from
/// `arranged_fixture_groups()` are grouped together (one group per component).
/// Tests with no shared fixture dependency stay in `remaining`.
///
/// Returns a [`FixturePartition`] where `arranged[i]` contains the
/// module groups for fixture component `i`.
pub(super) fn partition_by_fixture_groups(
    groups: Vec<ModuleGroup>,
    fixture_groups: &[Vec<String>],
) -> FixturePartition {
    if fixture_groups.is_empty() {
        return FixturePartition {
            arranged: vec![],
            remaining: groups,
        };
    }

    // Build fixture→group_index map: O(total fixtures across all groups)
    let fixture_to_group: std::collections::HashMap<&str, usize> = fixture_groups
        .iter()
        .enumerate()
        .flat_map(|(gi, fg)| fg.iter().map(move |f| (f.as_str(), gi)))
        .collect();

    let mut arranged: Vec<Vec<ModuleGroup>> = vec![vec![]; fixture_groups.len()];
    let mut remaining = Vec::new();

    for ModuleGroup { module_path, items } in groups {
        let mut group_buckets: Vec<Vec<Arc<TestItem>>> = vec![vec![]; fixture_groups.len()];
        let mut unassigned: Vec<Arc<TestItem>> = Vec::new();

        for item in items {
            let group = item
                .fixture_deps
                .iter()
                .find_map(|(q, _)| fixture_to_group.get(q.as_str()).copied());
            match group {
                Some(gi) => group_buckets[gi].push(Arc::clone(&item)),
                None => unassigned.push(item),
            }
        }

        for (gi, bucket) in group_buckets.into_iter().enumerate() {
            if !bucket.is_empty() {
                arranged[gi].push(ModuleGroup::new(module_path.clone(), bucket));
            }
        }
        if !unassigned.is_empty() {
            remaining.push(ModuleGroup::new(module_path, unassigned));
        }
    }

    FixturePartition {
        arranged,
        remaining,
    }
}

// ── ExecutionPlan value object ──────────────────────────────────────────────

/// How remaining (non-inprocess) tests should be dispatched.
#[derive(Debug)]
pub(super) enum ExecutionStrategy {
    Serial,
    Parallel { worker_count: usize },
}

/// A fully computed, pure execution plan.
///
/// Produced by [`plan_execution`] — no I/O, no PyO3.  The caller in
/// `execution.rs` dispatches based on this plan, spawning workers only
/// after the plan is finalised.
#[derive(Debug)]
pub(super) struct ExecutionPlan {
    pub strategy: ExecutionStrategy,
    /// Tests marked `inprocess` — always run on the main process.
    pub inprocess_groups: Vec<ModuleGroup>,
    /// Fixture-arranged groups that must run serially on the main process.
    pub arranged_groups: Vec<Vec<ModuleGroup>>,
    /// Remaining groups dispatched according to `strategy`.
    pub parallel_groups: Vec<ModuleGroup>,
}

/// Build an [`ExecutionPlan`] from pre-computed inputs.
///
/// Pure function: no I/O, no PyO3.  All PyO3-dependent data (fixture groups,
/// estimated duration) must be resolved by the caller.
#[allow(clippy::too_many_arguments)]
pub(super) fn plan_execution(
    groups: Vec<ModuleGroup>,
    mode: &crate::config::ExecutionMode,
    worker_count_cfg: usize,
    spawn_overhead_ms: f64,
    min_parallel_tests: usize,
    arranged_fixture_groups: &[Vec<String>],
    estimated: Option<std::time::Duration>,
    cpu_count: usize,
) -> ExecutionPlan {
    let total_tests: usize = groups.iter().map(|g| g.items.len()).sum();

    let is_serial = mode.is_serial();
    let force_parallel = matches!(
        mode,
        crate::config::ExecutionMode::Parallel {
            workers: crate::config::WorkerCount::Fixed(_)
        }
    );
    let use_parallel = !is_serial
        && worker_count_cfg > 1
        && (force_parallel
            || match estimated {
                Some(est) => est.as_millis() as f64 > spawn_overhead_ms * worker_count_cfg as f64,
                None => total_tests >= min_parallel_tests,
            });

    if !use_parallel {
        return ExecutionPlan {
            strategy: ExecutionStrategy::Serial,
            inprocess_groups: vec![],
            arranged_groups: vec![],
            parallel_groups: groups,
        };
    }

    // Partition inprocess-marked tests.
    let InprocessPartition {
        inprocess: inprocess_groups,
        parallel: parallel_groups,
    } = partition_inprocess_groups(groups);

    let optimal_worker_count =
        crate::config::compute_optimal_workers(mode, cpu_count, estimated, spawn_overhead_ms);

    // Arrange by the components the tests declared with `@oxi.arrange`.
    //
    // No threshold guards this any more (#1848). The ratio fallback existed
    // because the component set was *inferred* from a lifetime tier and could
    // therefore swallow a suite nobody had asked to serialise. A component now
    // exists only where a test named a fixture, so collapsing to one is a
    // thing the user asked for.
    if !arranged_fixture_groups.is_empty() {
        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(parallel_groups, arranged_fixture_groups);

        return ExecutionPlan {
            strategy: ExecutionStrategy::Parallel {
                worker_count: optimal_worker_count,
            },
            inprocess_groups,
            arranged_groups: arranged,
            parallel_groups: remaining,
        };
    }

    ExecutionPlan {
        strategy: ExecutionStrategy::Parallel {
            worker_count: optimal_worker_count,
        },
        inprocess_groups,
        arranged_groups: vec![],
        parallel_groups,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scheduler::ModuleGroup;
    use crate::types::TestItem;
    use camino::Utf8PathBuf;
    use std::sync::Arc;

    #[test]
    fn test_partition_inprocess_groups_splits_mixed_module() {
        let normal = TestItem::builder_raw("test_a.py::test_normal").arc();
        let inproc = TestItem::builder_raw("test_a.py::test_serial")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let groups = vec![ModuleGroup::new(
            Utf8PathBuf::from("test_a.py"),
            vec![normal, inproc],
        )];

        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups);
        assert_eq!(inp.len(), 1, "one module in inprocess");
        assert_eq!(inp[0].items.len(), 1);
        assert_eq!(inp[0].items[0].node_id.as_ref(), "test_a.py::test_serial");
        assert_eq!(par.len(), 1, "one module in parallel");
        assert_eq!(par[0].items.len(), 1);
        assert_eq!(par[0].items[0].node_id.as_ref(), "test_a.py::test_normal");
    }

    #[test]
    fn test_partition_inprocess_groups_no_inprocess() {
        let a = TestItem::builder_raw("test_a.py::test_a").arc();
        let b = TestItem::builder_raw("test_a.py::test_b").arc();
        let groups = vec![ModuleGroup::new(Utf8PathBuf::from("test_a.py"), vec![a, b])];

        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups);
        assert!(inp.is_empty());
        assert_eq!(par.len(), 1);
        assert_eq!(par[0].items.len(), 2);
    }

    #[test]
    fn test_partition_inprocess_groups_all_inprocess() {
        let a = TestItem::builder_raw("test_a.py::test_a")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let b = TestItem::builder_raw("test_a.py::test_b")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let groups = vec![ModuleGroup::new(Utf8PathBuf::from("test_a.py"), vec![a, b])];

        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups);
        assert_eq!(inp.len(), 1);
        assert_eq!(inp[0].items.len(), 2);
        assert!(par.is_empty());
    }

    #[test]
    fn test_partition_inprocess_groups_multiple_modules() {
        let a_normal = TestItem::builder_raw("test_a.py::test_normal").arc();
        let a_inproc = TestItem::builder_raw("test_a.py::test_serial")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let b_normal = TestItem::builder_raw("test_b.py::test_b").arc();
        let groups = vec![
            ModuleGroup::new(Utf8PathBuf::from("test_a.py"), vec![a_normal, a_inproc]),
            ModuleGroup::new(Utf8PathBuf::from("test_b.py"), vec![b_normal]),
        ];

        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups);
        assert_eq!(inp.len(), 1, "only test_a.py has inprocess items");
        assert_eq!(par.len(), 2, "both modules have parallel items");
    }

    #[test]
    fn test_partition_by_fixture_groups_no_groups() {
        let a = TestItem::builder_raw("test_a.py::test_a").arc();
        let groups = vec![ModuleGroup::new(Utf8PathBuf::from("test_a.py"), vec![a])];
        let fixture_groups: Vec<Vec<String>> = vec![];

        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(groups, &fixture_groups);
        assert!(arranged.is_empty());
        assert_eq!(remaining.len(), 1);
    }

    #[test]
    fn test_partition_by_fixture_groups_splits_by_fixture() {
        let mut a = TestItem::builder_raw("test_a.py::test_db").build();
        a.fixture_deps = vec![("db".to_string(), "DB".to_string())];
        let mut b = TestItem::builder_raw("test_a.py::test_plain").build();
        b.fixture_deps = vec![];
        let groups = vec![ModuleGroup::new(
            Utf8PathBuf::from("test_a.py"),
            vec![Arc::new(a), Arc::new(b)],
        )];
        let fixture_groups = vec![vec!["db".to_string()]];

        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(groups, &fixture_groups);
        assert_eq!(arranged.len(), 1, "one fixture group");
        assert_eq!(arranged[0].len(), 1, "one module in fixture group");
        assert_eq!(arranged[0][0].items.len(), 1, "one test in fixture group");
        assert_eq!(
            arranged[0][0].items[0].node_id.as_ref(),
            "test_a.py::test_db"
        );
        assert_eq!(remaining.len(), 1, "one module with remaining");
        assert_eq!(remaining[0].items.len(), 1, "one test remaining");
        assert_eq!(
            remaining[0].items[0].node_id.as_ref(),
            "test_a.py::test_plain"
        );
    }

    #[test]
    fn test_partition_by_fixture_groups_transitive() {
        let mut a = TestItem::builder_raw("test_a.py::test_repo").build();
        a.fixture_deps = vec![("repo".to_string(), "Repo".to_string())];
        let mut b = TestItem::builder_raw("test_a.py::test_db").build();
        b.fixture_deps = vec![("db".to_string(), "DB".to_string())];
        let mut c = TestItem::builder_raw("test_a.py::test_plain").build();
        c.fixture_deps = vec![];
        let groups = vec![ModuleGroup::new(
            Utf8PathBuf::from("test_a.py"),
            vec![Arc::new(a), Arc::new(b), Arc::new(c)],
        )];
        let fixture_groups = vec![vec!["db".to_string(), "repo".to_string()]];

        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(groups, &fixture_groups);
        assert_eq!(arranged.len(), 1);
        assert_eq!(arranged[0].len(), 1, "one module in fixture group 0");
        assert_eq!(arranged[0][0].items.len(), 2, "two tests in that module");
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].items.len(), 1, "one plain test remaining");
    }
}
