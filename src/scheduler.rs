use camino::Utf8PathBuf;

use crate::types::TestItem;

/// A batch of tests from one source file, dispatched as a unit to one worker.
#[derive(Debug, Clone)]
pub struct ModuleGroup {
    pub module_path: Utf8PathBuf,
    pub items: Vec<TestItem>,
}

impl ModuleGroup {
    pub fn new(module_path: Utf8PathBuf, items: Vec<TestItem>) -> Self {
        Self { module_path, items }
    }
}

#[cfg(test)]
impl ModuleGroup {
    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

/// Work-stealing queue of module groups.
///
/// Groups are dispatched in insertion order. Callers are responsible for
/// pre-sorting groups (e.g., via `cache.sort_groups()`). Workers call `pop()`
/// atomically to claim the next group.
pub struct Scheduler {
    queue: parking_lot::Mutex<std::collections::VecDeque<ModuleGroup>>,
}

impl Scheduler {
    /// Build from a list of (path, items) groups. Preserves insertion order (cache already sorted by duration).
    pub fn new(groups: Vec<(Utf8PathBuf, Vec<TestItem>)>) -> Self {
        let groups: Vec<ModuleGroup> = groups
            .into_iter()
            .map(|(p, items)| ModuleGroup::new(p, items))
            .collect();
        Self {
            queue: parking_lot::Mutex::new(std::collections::VecDeque::from(groups)),
        }
    }

    /// Pop the next group. Returns `None` when the queue is empty.
    pub fn pop(&self) -> Option<ModuleGroup> {
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

    fn make_group(path: &str, count: usize) -> (Utf8PathBuf, Vec<TestItem>) {
        let p = Utf8PathBuf::from(path);
        let items = (0..count)
            .map(|i| TestItem {
                node_id: NodeId::new(path, &format!("test_{i}"), None),
                module_path: p.clone(),
                fn_name: format!("test_{i}"),
                lineno: i,
                markers: vec![],
                param_id: None,
                param_values: vec![],
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
    fn test_parking_lot_mutex_no_poison_on_lock() {
        // parking_lot::Mutex::lock() returns a guard directly — no Result, no .unwrap().
        // This test fails to compile without the parking_lot dep.
        let m: parking_lot::Mutex<u32> = parking_lot::Mutex::new(0);
        *m.lock() += 1;
        assert_eq!(*m.lock(), 1);
    }
}
