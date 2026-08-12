//! Pure partition functions for arrangement of test groups.
//!
//! These functions have zero PyO3 dependencies — they operate only on
//! in-memory `TestItem` collections and are fully testable in isolation.

use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::scheduler::ModuleGroup;
use crate::types::TestItem;

/// The declaring anchors whose subtree holds at least one `inprocess` item.
///
/// Returned rather than tested per module because the question is about the
/// *subtree*, not the module: a module with no marked test of its own still has
/// to follow its anchor, and it cannot know that from its own items.
///
/// An anchor with no marked test anywhere beneath it is absent, so a suite that
/// never combines the two features keeps every group exactly where it was.
fn hot_package_anchors(groups: &[ModuleGroup], declaring_dirs: &[Utf8PathBuf]) -> Vec<Utf8PathBuf> {
    declaring_dirs
        .iter()
        .filter(|dir| {
            groups.iter().any(|group| {
                group.module_path.starts_with(dir)
                    && group.items.iter().any(|item| item.markers.has_inprocess())
            })
        })
        .cloned()
        .collect()
}

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
///
/// `declaring_dirs` are the anchors of `lifetime="package"` declarations. A
/// subtree under one of them is kept whole — see [`hot_package_anchors`].
pub(super) fn partition_inprocess_groups(
    groups: Vec<ModuleGroup>,
    declaring_modules: &[String],
    declaring_dirs: &[Utf8PathBuf],
) -> InprocessPartition {
    let mut inprocess = Vec::new();
    let mut parallel = Vec::new();

    let hot = hot_package_anchors(&groups, declaring_dirs);

    for ModuleGroup { module_path, items } in groups {
        // A declaring *package* subtree must not span two dispatch phases, for
        // the same reason a declaring module must not: each phase owns a fixture
        // session, so a split builds the package fixture once in each and the
        // tier's exactly-once promise does not hold (#2058).
        //
        // The whole subtree follows the mark, not just the marked module. One
        // marked test anywhere under the anchor pulls every module beneath it to
        // the coordinator, because the anchor — not the module — is the unit the
        // fixture is keyed by.
        //
        // The cost is bounded and already reported: a declaring subtree collapses
        // onto a single worker whatever happens here, and
        // `warn_about_package_collapse` names the anchor when it does. What this
        // moves is that one worker's work onto the coordinator.
        if hot.iter().any(|dir| module_path.starts_with(dir)) {
            inprocess.push(ModuleGroup::new(module_path, items));
            continue;
        }

        // A module that can resolve a `lifetime="module"` fixture must not span
        // two dispatch phases: each phase owns a fixture session, so a split
        // builds that fixture twice and the tier's once-per-module promise does
        // not hold (#1750).
        //
        // The mark wins, rather than being dropped for the tier. `inprocess` is
        // a semantic the user asked for explicitly, and the cost of honouring it
        // here is small: the module's items already travelled together as one
        // `ModuleGroup`, so this moves one group to the coordinator rather than
        // sacrificing parallelism across the suite.
        let declares = declaring_modules.iter().any(|m| m == module_path.as_str());
        if declares && items.iter().any(|item| item.markers.has_inprocess()) {
            inprocess.push(ModuleGroup::new(module_path, items));
            continue;
        }

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
///
/// `declaring_dirs` are the anchors of `lifetime="package"` declarations. A
/// subtree under one of them travels whole into a single component, because
/// arrangement is the second of the two routes that can split it.
pub(super) fn partition_by_fixture_groups(
    groups: Vec<ModuleGroup>,
    fixture_groups: &[Vec<String>],
    declaring_modules: &[String],
    declaring_dirs: &[Utf8PathBuf],
    arranged_display: &std::collections::HashMap<String, String>,
) -> FixturePartition {
    if fixture_groups.is_empty() {
        return FixturePartition {
            arranged: vec![],
            remaining: groups,
        };
    }

    // Build fixture→group_index map: O(total fixtures across all groups)
    let mut fixture_to_group: std::collections::HashMap<&str, usize> = fixture_groups
        .iter()
        .enumerate()
        .flat_map(|(gi, fg)| fg.iter().map(move |f| (f.as_str(), gi)))
        .collect();

    // A component is keyed by the *registry* name, while the qualifier a type
    // entry leaves in `fixture_deps` is the *type* name the user wrote —
    // `TempDir` against a component keyed `_TempDirFixture`. Alias the spelling
    // onto the same group so the qualifier match finds it (#2045).
    //
    // Aliased here rather than by rewriting the qualifier at collection: that
    // qualifier is what `validate_fixture_names` reads, and it is already the
    // gate that refuses an `@injectable` type no fixture provides. Rewriting it
    // would move that refusal without replacing it.
    for (resolved, spelling) in arranged_display {
        if let Some(&gi) = fixture_to_group.get(resolved.as_str()) {
            fixture_to_group.insert(spelling.as_str(), gi);
        }
    }

    let mut arranged: Vec<Vec<ModuleGroup>> = vec![vec![]; fixture_groups.len()];
    let mut remaining = Vec::new();

    // Which component each declaring package subtree travels into, decided once
    // for the whole subtree (#2058).
    //
    // Arrangement is the second route that splits a subtree, and it needs no
    // mark to do it: `@oxi.arrange` on one module of a declaring package leaves
    // its siblings in the parallel remainder, which is two phases and so two
    // builds. ADR-0009 Amendment 15 recorded the identical pair one tier down.
    //
    // The subtree is kept *inside* a component rather than excluded from
    // arrangement altogether. Excluding it — which is what this code did before
    // #2058 — sends it to a worker and silently discards the co-location
    // `@oxi.arrange` asked for, which is the same objection
    // `test_arrange_groups_at_module_tier_on_the_runner` already raises for a
    // module.
    // A module reaches its anchor through `outermost_declaring_ancestor`, which
    // `group_by_package` already uses. Shallowest wins, so the answer does not
    // depend on the order the anchors arrive in — and the anchor rule stays
    // stated in one place rather than two.
    //
    // First matching component wins, scanning the subtree in group order. A
    // subtree cannot sit in two buckets, and both the group order and
    // `fixture_groups` order are deterministic, so the choice is stable — the
    // rule a declaring module already follows.
    let subtree_component: std::collections::HashMap<&Utf8PathBuf, Option<usize>> = declaring_dirs
        .iter()
        .map(|dir| {
            let component = groups
                .iter()
                .filter(|group| group.module_path.starts_with(dir))
                .flat_map(|group| group.items.iter())
                .find_map(|item| {
                    item.fixture_deps.iter().find_map(|(qualifier, _)| {
                        fixture_to_group.get(qualifier.as_str()).copied()
                    })
                });
            (dir, component)
        })
        .collect();

    for ModuleGroup { module_path, items } in groups {
        // The whole declaring subtree goes where its anchor goes (#2058).
        if let Some(anchor) =
            crate::filter::outermost_declaring_ancestor(&module_path, declaring_dirs)
        {
            match subtree_component.get(&anchor).copied().flatten() {
                Some(index) => arranged[index].push(ModuleGroup::new(module_path, items)),
                None => remaining.push(ModuleGroup::new(module_path, items)),
            }
            continue;
        }

        // A declaring module travels whole, and stays *arranged* if any of its
        // items asked to be (#1750).
        //
        // Decided before bucketing rather than moved back afterwards: moving
        // afterwards would reunite the items as *two* `ModuleGroup`s sharing one
        // path, which the scheduler may hand to two workers — two sessions
        // again, which is the defect this removes.
        //
        // First matching component wins when a module's items would fall into
        // two. A module is one scheduling unit and cannot sit in two buckets,
        // and `fixture_groups` order is deterministic, so the choice is stable.
        if declaring_modules.iter().any(|m| m == module_path.as_str()) {
            let bucket = items.iter().find_map(|item| {
                item.fixture_deps
                    .iter()
                    .find_map(|(q, _)| fixture_to_group.get(q.as_str()).copied())
            });
            match bucket {
                Some(gi) => arranged[gi].push(ModuleGroup::new(module_path, items)),
                None => remaining.push(ModuleGroup::new(module_path, items)),
            }
            continue;
        }

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
    declaring_modules: &[String],
    declaring_dirs: &[Utf8PathBuf],
    arranged_display: &std::collections::HashMap<String, String>,
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
    } = partition_inprocess_groups(groups, declaring_modules, declaring_dirs);

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
        } = partition_by_fixture_groups(
            parallel_groups,
            arranged_fixture_groups,
            declaring_modules,
            declaring_dirs,
            arranged_display,
        );

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
        } = partition_inprocess_groups(groups, &[], &[]);
        assert_eq!(inp.len(), 1, "one module in inprocess");
        assert_eq!(inp[0].items.len(), 1);
        assert_eq!(inp[0].items[0].node_id.as_ref(), "test_a.py::test_serial");
        assert_eq!(par.len(), 1, "one module in parallel");
        assert_eq!(par[0].items.len(), 1);
        assert_eq!(par[0].items[0].node_id.as_ref(), "test_a.py::test_normal");
    }

    #[test]
    fn test_a_declaring_module_follows_the_inprocess_mark() {
        let normal = TestItem::builder_raw("pkg/test_a.py::test_normal").arc();
        let inproc = TestItem::builder_raw("pkg/test_a.py::test_serial")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let groups = vec![ModuleGroup::new(
            Utf8PathBuf::from("pkg/test_a.py"),
            vec![normal, inproc],
        )];
        let declaring = ["pkg/test_a.py".to_string()];

        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups, &declaring, &[]);

        assert_eq!(
            inp.len(),
            1,
            "the declaring module goes wholly in-process, as one group"
        );
        assert_eq!(
            inp[0].items.len(),
            2,
            "both items travel together — splitting them puts the module's fixture in \
             two sessions, which is the #1750 double build"
        );
        assert!(
            par.is_empty(),
            "nothing is left in the parallel set; a remainder here is the second session"
        );
    }

    #[test]
    fn test_a_declaring_package_subtree_follows_an_inprocess_mark_anywhere_beneath_it() {
        // Arrange — two modules under one anchor; only the second carries a mark.
        let plain = TestItem::builder_raw("pkg/test_a.py::test_plain").arc();
        let marked = TestItem::builder_raw("pkg/sub/test_b.py::test_marked")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let groups = vec![
            ModuleGroup::new(Utf8PathBuf::from("pkg/test_a.py"), vec![plain]),
            ModuleGroup::new(Utf8PathBuf::from("pkg/sub/test_b.py"), vec![marked]),
        ];
        let declaring_dirs = [Utf8PathBuf::from("pkg")];

        // Act
        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups, &[], &declaring_dirs);

        // Assert
        assert_eq!(
            inp.len(),
            2,
            "the unmarked module must follow its anchor; leaving it behind puts the \
             package fixture in two sessions, which is the #2058 double build"
        );
        assert!(
            par.is_empty(),
            "a remainder here is the second dispatch phase the tier cannot survive"
        );
    }

    #[test]
    fn test_a_declaring_package_subtree_without_a_mark_keeps_its_parallelism() {
        // Arrange — an anchor whose subtree carries no inprocess mark at all.
        let plain = TestItem::builder_raw("pkg/test_a.py::test_plain").arc();
        let groups = vec![ModuleGroup::new(
            Utf8PathBuf::from("pkg/test_a.py"),
            vec![plain],
        )];
        let declaring_dirs = [Utf8PathBuf::from("pkg")];

        // Act
        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups, &[], &declaring_dirs);

        // Assert
        assert!(
            inp.is_empty(),
            "declaring a package fixture must not by itself move work onto the \
             coordinator. Without this scoping every declaring package in a suite \
             would serialise onto the runner — a cost larger than the defect being \
             fixed, and one no exactly-once assertion can detect"
        );
        assert_eq!(par.len(), 1, "the module stays eligible for a worker");
    }

    #[test]
    fn test_an_unrelated_subtree_is_untouched_by_a_declaring_neighbour() {
        // Arrange — a marked module that shares a name prefix but not an anchor.
        let marked = TestItem::builder_raw("pkg_other/test_b.py::test_marked")
            .markers(vec!["inprocess".to_string()])
            .arc();
        let plain = TestItem::builder_raw("pkg/test_a.py::test_plain").arc();
        let groups = vec![
            ModuleGroup::new(Utf8PathBuf::from("pkg/test_a.py"), vec![plain]),
            ModuleGroup::new(Utf8PathBuf::from("pkg_other/test_b.py"), vec![marked]),
        ];
        let declaring_dirs = [Utf8PathBuf::from("pkg")];

        // Act
        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups, &[], &declaring_dirs);

        // Assert — `pkg_other` is not inside `pkg`, so the anchor is not hot.
        assert_eq!(
            inp.len(),
            1,
            "only the genuinely marked module goes in-process; matching on a shared \
             name prefix would drag an unrelated subtree onto the coordinator"
        );
        assert_eq!(
            inp[0].module_path.as_str(),
            "pkg_other/test_b.py",
            "the marked module is the one that moved"
        );
        assert_eq!(par.len(), 1, "the declaring subtree keeps its worker");
    }

    #[test]
    fn test_a_declaring_module_stays_whole_inside_its_component() {
        let arranged_item = TestItem::builder_raw("pkg/test_a.py::test_one")
            .fixture_deps(vec![("side".to_string(), String::new())])
            .arc();
        let plain = TestItem::builder_raw("pkg/test_a.py::test_two").arc();
        let groups = vec![ModuleGroup::new(
            Utf8PathBuf::from("pkg/test_a.py"),
            vec![arranged_item, plain],
        )];
        let components = vec![vec!["side".to_string()]];
        let declaring = ["pkg/test_a.py".to_string()];

        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(
            groups,
            &components,
            &declaring,
            &[],
            &std::collections::HashMap::new(),
        );

        assert_eq!(
            arranged[0].len(),
            1,
            "the module stays arranged — excluding it would send it to a worker and \
             drop the co-location @oxi.arrange promises"
        );
        assert_eq!(
            arranged[0][0].items.len(),
            2,
            "and it travels whole, so the item that named no fixture is not stranded \
             in the parallel remainder in a second session"
        );
        assert!(
            remaining.is_empty(),
            "nothing is left behind; a remainder here is that second session"
        );
    }

    #[test]
    fn test_a_declaring_package_subtree_travels_whole_into_one_component() {
        // Arrange — two modules under one anchor; only the first is arranged.
        let arranged_item = TestItem::builder_raw("pkg/test_a.py::test_one")
            .fixture_deps(vec![("side".to_string(), String::new())])
            .arc();
        let plain = TestItem::builder_raw("pkg/sub/test_b.py::test_two").arc();
        let groups = vec![
            ModuleGroup::new(Utf8PathBuf::from("pkg/test_a.py"), vec![arranged_item]),
            ModuleGroup::new(Utf8PathBuf::from("pkg/sub/test_b.py"), vec![plain]),
        ];
        let components = vec![vec!["side".to_string()]];
        let declaring_dirs = [Utf8PathBuf::from("pkg")];

        // Act
        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(
            groups,
            &components,
            &[],
            &declaring_dirs,
            &std::collections::HashMap::new(),
        );

        // Assert
        assert_eq!(
            arranged[0].len(),
            2,
            "the unarranged sibling must follow its anchor into the component; leaving \
             it in the remainder is the second phase that double-builds the package fixture"
        );
        assert!(
            remaining.is_empty(),
            "a remainder here is that second phase"
        );
    }

    #[test]
    fn test_the_outermost_anchor_owns_the_subtree_whatever_order_the_anchors_arrive_in() {
        // Arrange — a package and its own subpackage both declare, and the
        // *inner* anchor is listed first. The order is deliberate, and it is the
        // order `group_by_package_gives_the_outermost_declaration_the_subtree`
        // uses for the same reason: an ancestor happens to sort before its
        // descendant today, so an in-order test cannot tell a rule that reads
        // the depth from one that reads the position.
        let outer_item = TestItem::builder_raw("pkg/test_a.py::test_one")
            .fixture_deps(vec![("side".to_string(), String::new())])
            .arc();
        let inner_item = TestItem::builder_raw("pkg/inner/test_b.py::test_two").arc();
        let groups = vec![
            ModuleGroup::new(Utf8PathBuf::from("pkg/test_a.py"), vec![outer_item]),
            ModuleGroup::new(Utf8PathBuf::from("pkg/inner/test_b.py"), vec![inner_item]),
        ];
        let components = vec![vec!["side".to_string()]];
        let declaring_dirs = [Utf8PathBuf::from("pkg/inner"), Utf8PathBuf::from("pkg")];

        // Act
        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(
            groups,
            &components,
            &[],
            &declaring_dirs,
            &std::collections::HashMap::new(),
        );

        // Assert
        assert_eq!(
            arranged[0].len(),
            2,
            "the outermost anchor owns the whole subtree. Honouring the inner \
             anchor instead splits its ancestor across two phases, which is the \
             double build this tier exists to prevent"
        );
        assert!(
            remaining.is_empty(),
            "a module stranded here is that second phase"
        );
    }

    #[test]
    fn test_a_declaring_package_subtree_that_arranges_nothing_stays_whole_in_the_remainder() {
        // Arrange — an anchor whose subtree names no arranged fixture at all.
        let plain_a = TestItem::builder_raw("pkg/test_a.py::test_one").arc();
        let plain_b = TestItem::builder_raw("pkg/sub/test_b.py::test_two").arc();
        let groups = vec![
            ModuleGroup::new(Utf8PathBuf::from("pkg/test_a.py"), vec![plain_a]),
            ModuleGroup::new(Utf8PathBuf::from("pkg/sub/test_b.py"), vec![plain_b]),
        ];
        let components = vec![vec!["side".to_string()]];
        let declaring_dirs = [Utf8PathBuf::from("pkg")];

        // Act
        let FixturePartition {
            arranged,
            remaining,
        } = partition_by_fixture_groups(
            groups,
            &components,
            &[],
            &declaring_dirs,
            &std::collections::HashMap::new(),
        );

        // Assert
        assert!(
            arranged[0].is_empty(),
            "a subtree that asked for no arrangement must not be pulled onto the runner"
        );
        assert_eq!(
            remaining.len(),
            2,
            "it stays whole in the remainder, where `group_by_package` merges it"
        );
    }

    #[test]
    fn test_partition_inprocess_groups_no_inprocess() {
        let a = TestItem::builder_raw("test_a.py::test_a").arc();
        let b = TestItem::builder_raw("test_a.py::test_b").arc();
        let groups = vec![ModuleGroup::new(Utf8PathBuf::from("test_a.py"), vec![a, b])];

        let InprocessPartition {
            inprocess: inp,
            parallel: par,
        } = partition_inprocess_groups(groups, &[], &[]);
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
        } = partition_inprocess_groups(groups, &[], &[]);
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
        } = partition_inprocess_groups(groups, &[], &[]);
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
        } = partition_by_fixture_groups(
            groups,
            &fixture_groups,
            &[],
            &[],
            &std::collections::HashMap::new(),
        );
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
        } = partition_by_fixture_groups(
            groups,
            &fixture_groups,
            &[],
            &[],
            &std::collections::HashMap::new(),
        );
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
        } = partition_by_fixture_groups(
            groups,
            &fixture_groups,
            &[],
            &[],
            &std::collections::HashMap::new(),
        );
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
        plan_execution(
            groups,
            mode,
            4,
            250.0,
            1,
            arranged_fixture_groups,
            &[],
            &[],
            &std::collections::HashMap::new(),
            None,
            8,
        )
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
