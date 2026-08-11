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

    // ── plan_execution ───────────────────────────────────────────────────────
    //
    // `plan_execution` is pure by construction — its doc comment says so, and
    // every PyO3-dependent input is resolved by the caller. Nothing tested it
    // directly before #1848, which `codecov/patch/rust` reported the moment the
    // arranged branch was restructured and its lines re-stamped as added. The
    // behaviour is covered end to end by python/tests/test_arrange_scheduling.py,
    // which drives the real runner as a subprocess and so contributes nothing to
    // the Rust flag.

    /// A module group of `count` plain tests, named after `module`.
    fn plain_group(module: &str, count: usize) -> ModuleGroup {
        ModuleGroup::new(
            Utf8PathBuf::from(module),
            (0..count)
                .map(|i| TestItem::builder_raw(&format!("{module}::test_{i}")).arc())
                .collect::<Vec<_>>(),
        )
    }

    /// A module group whose single test carries `fixture` in `fixture_deps`.
    ///
    /// The empty type name matches what `_augment_fixture_deps` writes for a
    /// string entry: only the qualifier is set, and the qualifier is what
    /// `partition_by_fixture_groups` matches on.
    fn group_using(module: &str, fixture: &str) -> ModuleGroup {
        let mut item = TestItem::builder_raw(&format!("{module}::test_uses")).build();
        item.fixture_deps = vec![(fixture.to_string(), String::new())];
        ModuleGroup::new(Utf8PathBuf::from(module), vec![Arc::new(item)])
    }

    /// `plan_execution` with the knobs these tests never vary held fixed.
    fn plan(
        groups: Vec<ModuleGroup>,
        mode: &crate::config::ExecutionMode,
        arranged_fixture_groups: &[Vec<String>],
    ) -> ExecutionPlan {
        plan_execution(groups, mode, 4, 250.0, 1, arranged_fixture_groups, None, 8)
    }

    fn parallel_mode() -> crate::config::ExecutionMode {
        crate::config::ExecutionMode::Parallel {
            workers: crate::config::WorkerCount::Fixed(4),
        }
    }

    #[test]
    fn plan_execution_arranges_the_groups_that_name_a_component() {
        let groups = vec![
            group_using("test_a.py", "dsn"),
            group_using("test_b.py", "dsn"),
            plain_group("test_c.py", 1),
        ];

        let plan = plan(groups, &parallel_mode(), &[vec!["dsn".to_string()]]);

        assert!(
            matches!(plan.strategy, ExecutionStrategy::Parallel { .. }),
            "a component does not force serial since #1848 removed the ratio \
             fallback; the remaining groups must still go to workers"
        );
        assert_eq!(
            plan.arranged_groups.len(),
            1,
            "one component was declared, so there is one arranged bucket"
        );
        assert_eq!(
            plan.arranged_groups[0].len(),
            2,
            "both modules naming the component co-locate: {:?}",
            plan.arranged_groups[0]
                .iter()
                .map(|g| g.module_path.as_str())
                .collect::<Vec<_>>()
        );
        assert_eq!(
            plan.parallel_groups.len(),
            1,
            "the module naming nothing stays parallel-eligible"
        );
    }

    #[test]
    fn plan_execution_arranges_nothing_when_no_component_is_declared() {
        let groups = vec![plain_group("test_a.py", 1), plain_group("test_b.py", 1)];

        let plan = plan(groups, &parallel_mode(), &[]);

        assert!(
            plan.arranged_groups.is_empty(),
            "with no @oxi.arrange anywhere there is no component to arrange — \
             this is the retired inference, and its absence is the point of #1848"
        );
        assert_eq!(
            plan.parallel_groups.len(),
            2,
            "every group stays parallel-eligible"
        );
    }

    #[test]
    fn plan_execution_ignores_components_when_the_run_is_serial() {
        let groups = vec![group_using("test_a.py", "dsn")];

        let plan = plan(
            groups,
            &crate::config::ExecutionMode::Serial,
            &[vec!["dsn".to_string()]],
        );

        assert!(
            matches!(plan.strategy, ExecutionStrategy::Serial),
            "--serial wins over any arrangement"
        );
        assert!(
            plan.arranged_groups.is_empty(),
            "arrangement co-locates onto the main process, which a serial run \
             already does, so the bucket must stay empty rather than splitting \
             the run into phases that cannot differ"
        );
        assert_eq!(plan.parallel_groups.len(), 1, "the group runs, serially");
    }
}
