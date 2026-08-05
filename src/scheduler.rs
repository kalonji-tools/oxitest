use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::cache::TestCache;
use crate::config::ScheduleStrategy;
use crate::types::TestItem;

/// Sort groups according to the chosen scheduling strategy.
///
/// Called *before* `Scheduler::new()` — the scheduler itself always
/// processes groups in insertion order, so the ordering happens here.
pub(crate) fn apply_schedule_strategy(
    groups: &mut Vec<ModuleGroup>,
    strategy: ScheduleStrategy,
    cache: &TestCache,
    failed_ids: &std::collections::HashSet<String>,
) {
    match strategy {
        ScheduleStrategy::LongestFirst => {
            cache.sort_groups(groups);
        }
        ScheduleStrategy::FailedFirst => {
            cache.sort_groups(groups);
            groups.sort_by_key(|g| {
                !g.items
                    .iter()
                    .any(|item| failed_ids.contains(item.node_id.as_ref()))
            });
        }
        ScheduleStrategy::Random => {
            use rand::seq::SliceRandom;
            let mut rng = rand::rng();
            groups.shuffle(&mut rng);
        }
    }
}

/// A batch of tests from one source file, dispatched as a unit to one worker.
#[derive(Debug, Clone)]
pub(crate) struct ModuleGroup {
    pub(crate) module_path: Utf8PathBuf,
    pub(crate) items: Vec<Arc<TestItem>>,
}

impl ModuleGroup {
    pub(crate) fn new(module_path: Utf8PathBuf, items: Vec<Arc<TestItem>>) -> Self {
        Self { module_path, items }
    }
}

/// What one worker task covers: one or more modules run in a single process.
///
/// [`ModuleGroup`] stays exactly what its name says — one module and its items.
/// This wraps N of them, mirroring `WorkerTask { modules: Vec<WorkerTaskModule> }`
/// so `build_task` is a 1:1 map. `group_by_package` (#1710) merges a declaring
/// package's subtree into one of these; every other group stays a singleton.
#[derive(Debug, Clone)]
pub(crate) struct TaskGroup {
    pub(crate) modules: Vec<ModuleGroup>,
    /// The declaring anchor directory whose subtree this group covers.
    ///
    /// `Some` exactly for the groups `group_by_package` merged, and it holds
    /// the same directory the collector handed Python as
    /// `anchor_package_path` — which is what `_package_scopes` is keyed by, so
    /// this is the only value `end_package` can be called with (#1839).
    /// `None` for a module no package-lifetime declaration covers: it has no
    /// package boundary, so there is nothing to fire at one.
    pub(crate) anchor: Option<Utf8PathBuf>,
}

impl TaskGroup {
    /// Wrap a single module — the shape every group has until #1710.
    pub(crate) fn single(group: ModuleGroup) -> Self {
        Self {
            modules: vec![group],
            anchor: None,
        }
    }

    /// A declaring package's subtree, merged under the anchor that declared it.
    pub(crate) fn package(anchor: Utf8PathBuf, modules: Vec<ModuleGroup>) -> Self {
        Self {
            modules,
            anchor: Some(anchor),
        }
    }

    /// Total items across every module.
    ///
    /// This is what the coordinator waits for. Undercount it and `drain_results`
    /// returns before the worker has finished, leaving results unread and the run
    /// waiting on a watchdog that never trips.
    pub(crate) fn item_count(&self) -> usize {
        self.modules.iter().map(|m| m.items.len()).sum()
    }

    /// Every item across every module, for in-flight tracking and crash synthesis.
    pub(crate) fn items(&self) -> impl Iterator<Item = &Arc<TestItem>> {
        self.modules.iter().flat_map(|m| m.items.iter())
    }

    /// Label for diagnostics — the group's first module.
    ///
    /// Diagnostics only. It is a *file* path, never a package boundary key —
    /// passing it to `end_package` is #1839, and [`Self::anchor`] is the field
    /// that answers that question.
    ///
    /// Every constructor guarantees at least one module: [`Self::single`] takes
    /// one, and `group_by_package` only ever builds a group from a non-empty
    /// merge. Indexing states that invariant; inventing a placeholder path
    /// would let an impossible group reach a diagnostic looking like a real
    /// directory name.
    pub(crate) fn label(&self) -> &camino::Utf8Path {
        self.modules[0].module_path.as_path()
    }
}

/// Work-stealing queue of task groups.
///
/// Groups are dispatched in insertion order. Callers are responsible for
/// pre-sorting groups (e.g., via `cache.sort_groups()`). Workers call `pop()`
/// atomically to claim the next group.
pub(crate) struct Scheduler {
    queue: parking_lot::Mutex<std::collections::VecDeque<TaskGroup>>,
}

impl Scheduler {
    /// Build from a list of task groups. Preserves insertion order (cache already sorted by duration).
    pub(crate) fn new(groups: Vec<TaskGroup>) -> Self {
        Self {
            queue: parking_lot::Mutex::new(std::collections::VecDeque::from(groups)),
        }
    }

    /// Pop the next group. Returns `None` when the queue is empty.
    pub(crate) fn pop(&self) -> Option<TaskGroup> {
        self.queue.lock().pop_front()
    }
}

#[cfg(test)]
impl Scheduler {
    pub fn remaining(&self) -> usize {
        self.queue.lock().len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_group(path: &str, count: usize) -> ModuleGroup {
        let p = Utf8PathBuf::from(path);
        let items = (0..count)
            .map(|i| {
                Arc::new(
                    TestItem::builder(path, &format!("test_{i}"))
                        .lineno(i)
                        .build(),
                )
            })
            .collect();
        ModuleGroup::new(p, items)
    }

    /// A singleton task group — the shape everything has until #1710.
    fn make_task(path: &str, count: usize) -> TaskGroup {
        TaskGroup::single(make_group(path, count))
    }

    #[test]
    fn task_group_item_count_sums_every_module() {
        // Arrange
        let group = TaskGroup::package(
            Utf8PathBuf::from("pkg"),
            vec![make_group("a.py", 3), make_group("b.py", 2)],
        );

        // Act / Assert — the coordinator drains exactly this many result lines,
        // so a short count returns early and hangs the run on a watchdog that
        // never trips.
        assert_eq!(
            group.item_count(),
            5,
            "item_count must sum across every module, not report one module's total"
        );
    }

    #[test]
    fn task_group_items_yields_every_module_in_order() {
        // Arrange
        let group = TaskGroup::package(
            Utf8PathBuf::from("pkg"),
            vec![make_group("a.py", 2), make_group("b.py", 1)],
        );

        // Act
        let paths: Vec<&str> = group.items().map(|i| i.module_path()).collect();

        // Assert — in-flight tracking and crash synthesis both walk this; missing
        // a module's items would leave those tests unaccounted for on a crash.
        assert_eq!(
            paths,
            vec!["a.py", "a.py", "b.py"],
            "items() must walk every module in group order"
        );
    }

    #[test]
    fn test_scheduler_preserves_insertion_order() {
        // c=1 is last by count but first in insertion order.
        // After fix: c.py must come out first.
        // Before fix (count sort): b.py would come out first.
        let groups = vec![
            make_task("c.py", 1), // smallest by count, first by insertion
            make_task("b.py", 5),
            make_task("a.py", 2),
        ];
        let sched = Scheduler::new(groups);
        let first = sched.pop().unwrap();
        assert_eq!(
            first.modules[0].module_path,
            Utf8PathBuf::from("c.py"),
            "Scheduler must preserve insertion order — cache already sorted by duration"
        );
    }

    #[test]
    fn test_scheduler_pop_drains_queue() {
        let groups = vec![make_task("a.py", 1), make_task("b.py", 2)];
        let sched = Scheduler::new(groups);
        assert_eq!(sched.remaining(), 2);
        sched.pop();
        assert_eq!(sched.remaining(), 1);
        sched.pop();
        assert_eq!(sched.remaining(), 0);
        assert!(sched.pop().is_none());
    }

    #[test]
    fn test_scheduler_empty_returns_none() {
        let sched = Scheduler::new(vec![]);
        assert!(sched.pop().is_none());
    }

    #[test]
    fn test_scheduler_single_group() {
        let groups = vec![make_task("only.py", 3)];
        let sched = Scheduler::new(groups);
        let g = sched.pop().unwrap();
        assert_eq!(g.item_count(), 3);
        assert!(sched.pop().is_none());
    }

    #[test]
    fn test_apply_strategy_longest_first_uses_cache_sort() {
        use crate::cache::TestCache;
        use crate::config::ScheduleStrategy;
        use std::collections::HashSet;

        let cache = TestCache::empty_for_test();
        // With empty cache, LongestFirst falls back to item count (3 > 1)
        let mut groups = vec![make_group("fast.py", 1), make_group("slow.py", 3)];
        let failed: HashSet<String> = HashSet::new();

        apply_schedule_strategy(&mut groups, ScheduleStrategy::LongestFirst, &cache, &failed);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("slow.py"));
    }

    #[test]
    fn test_apply_strategy_failed_first_moves_failed_groups_to_front() {
        use crate::cache::TestCache;
        use crate::config::ScheduleStrategy;
        use std::collections::HashSet;

        let cache = TestCache::empty_for_test();
        let mut groups = vec![make_group("clean.py", 2), make_group("broken.py", 1)];
        let mut failed: HashSet<String> = HashSet::new();
        failed.insert("broken.py::test_0".to_string());

        apply_schedule_strategy(&mut groups, ScheduleStrategy::FailedFirst, &cache, &failed);
        assert_eq!(groups[0].module_path, Utf8PathBuf::from("broken.py"));
    }

    #[test]
    fn test_apply_strategy_random_preserves_all_groups() {
        use crate::cache::TestCache;
        use crate::config::ScheduleStrategy;
        use std::collections::HashSet;

        let cache = TestCache::empty_for_test();
        let failed: HashSet<String> = HashSet::new();

        let mut groups: Vec<_> = (0..5)
            .map(|i| make_group(&format!("mod_{i}.py"), 1))
            .collect();
        apply_schedule_strategy(&mut groups, ScheduleStrategy::Random, &cache, &failed);
        // All 5 groups must still be present regardless of order
        assert_eq!(groups.len(), 5);
    }

    #[test]
    fn test_parking_lot_mutex_no_poison_on_lock() {
        // parking_lot::Mutex::lock() returns a guard directly — no Result, no .unwrap().
        // This test fails to compile without the parking_lot dep.
        let m: parking_lot::Mutex<u32> = parking_lot::Mutex::new(0);
        *m.lock() += 1;
        assert_eq!(*m.lock(), 1);
    }
}
