use std::sync::Arc;

use camino::Utf8PathBuf;

use crate::cache::TimingCache;
use crate::config::ScheduleStrategy;
use crate::types::TestItem;

/// Sort groups according to the chosen scheduling strategy.
///
/// Called *before* `Scheduler::new()` — the scheduler itself always
/// processes groups in insertion order, so the ordering happens here.
pub(crate) fn apply_schedule_strategy(
    groups: &mut Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>,
    strategy: ScheduleStrategy,
    cache: &impl TimingCache,
    failed_ids: &std::collections::HashSet<String>,
) {
    match strategy {
        ScheduleStrategy::LongestFirst => {
            cache.sort_groups(groups);
        }
        ScheduleStrategy::FailedFirst => {
            cache.sort_groups(groups);
            groups.sort_by_key(|(_, items)| {
                !items
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

#[cfg(test)]
impl ModuleGroup {
    pub fn len(&self) -> usize {
        self.items.len()
    }
}

/// Work-stealing queue of module groups.
///
/// Groups are dispatched in insertion order. Callers are responsible for
/// pre-sorting groups (e.g., via `cache.sort_groups()`). Workers call `pop()`
/// atomically to claim the next group.
pub(crate) struct Scheduler {
    queue: parking_lot::Mutex<std::collections::VecDeque<ModuleGroup>>,
}

impl Scheduler {
    /// Build from a list of (path, items) groups. Preserves insertion order (cache already sorted by duration).
    pub(crate) fn new(groups: Vec<(Utf8PathBuf, Vec<Arc<TestItem>>)>) -> Self {
        let groups: Vec<ModuleGroup> = groups
            .into_iter()
            .map(|(p, items)| ModuleGroup::new(p, items))
            .collect();
        Self {
            queue: parking_lot::Mutex::new(std::collections::VecDeque::from(groups)),
        }
    }

    /// Pop the next group. Returns `None` when the queue is empty.
    pub(crate) fn pop(&self) -> Option<ModuleGroup> {
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
    use crate::types::NodeId;

    fn make_group(path: &str, count: usize) -> (Utf8PathBuf, Vec<Arc<TestItem>>) {
        let p = Utf8PathBuf::from(path);
        let items = (0..count)
            .map(|i| {
                Arc::new(TestItem {
                    node_id: NodeId::new(path, &format!("test_{i}"), None),
                    module_path: p.clone(),
                    fn_name: format!("test_{i}"),
                    lineno: crate::types::LineNo::new(i),
                    markers: vec![],
                    param_id: None,
                    param_values: vec![],
                    is_async: false,
                    fixture_names: vec![],
                })
            })
            .collect();
        (p, items)
    }

    #[test]
    fn test_scheduler_preserves_insertion_order() {
        // c=1 is last by count but first in insertion order.
        // After fix: c.py must come out first.
        // Before fix (count sort): b.py would come out first.
        let groups = vec![
            make_group("c.py", 1), // smallest by count, first by insertion
            make_group("b.py", 5),
            make_group("a.py", 2),
        ];
        let sched = Scheduler::new(groups);
        let first = sched.pop().unwrap();
        assert_eq!(
            first.module_path,
            Utf8PathBuf::from("c.py"),
            "Scheduler must preserve insertion order — cache already sorted by duration"
        );
    }

    #[test]
    fn test_scheduler_pop_drains_queue() {
        let groups = vec![make_group("a.py", 1), make_group("b.py", 2)];
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
        let groups = vec![make_group("only.py", 3)];
        let sched = Scheduler::new(groups);
        let g = sched.pop().unwrap();
        assert_eq!(g.len(), 3);
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
        assert_eq!(groups[0].0, Utf8PathBuf::from("slow.py"));
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
        assert_eq!(groups[0].0, Utf8PathBuf::from("broken.py"));
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
