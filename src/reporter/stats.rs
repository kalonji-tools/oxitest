use std::sync::Arc;

use crate::types::{DurationMs, NodeId, TestItem, TestOutcome};

/// Per-fixture cache hit/miss entry.
#[derive(Clone, Debug)]
pub(crate) struct FixtureCacheEntry {
    pub(crate) name: String,
    pub(crate) hits: usize,
    pub(crate) misses: usize,
}

/// Aggregate fixture cache statistics returned by the bridge.
#[derive(Clone, Debug)]
pub(crate) struct FixtureCacheStats {
    pub(crate) hits: usize,
    pub(crate) misses: usize,
    pub(crate) breakdown: Vec<FixtureCacheEntry>,
}

impl FixtureCacheStats {
    /// Format the fixture cache summary line.
    pub(crate) fn summary(&self) -> String {
        let total = self.hits + self.misses;
        if total == 0 {
            return "shared fixture cache: no fixtures used".to_string();
        }
        let pct = 100 * self.hits / total;
        format!(
            "shared fixture cache: {}/{} hits ({}%)",
            self.hits, total, pct
        )
    }
}

/// Per-fixture timing aggregate returned by the Python bridge.
#[derive(Clone, Debug)]
pub(crate) struct FixtureTimingEntry {
    pub(crate) name: String,
    pub(crate) total_setup: DurationMs,
    pub(crate) setup_count: usize,
    pub(crate) total_teardown: DurationMs,
    pub(crate) teardown_count: usize,
}

impl FixtureTimingEntry {
    /// Total time (setup + teardown) for sorting.
    pub(crate) fn total(&self) -> DurationMs {
        self.total_setup + self.total_teardown
    }
}

/// Timing record for a single test execution.
#[derive(Clone, Debug)]
pub(crate) struct TimingEntry {
    pub(crate) node_id: NodeId,
    pub(crate) duration_ms: DurationMs,
}

/// A print() call inside a passing test (potential debugging leftover).
#[derive(Clone, Debug)]
pub(crate) struct TipLine {
    pub(crate) file: camino::Utf8PathBuf,
    pub(crate) lineno: crate::types::LineNo,
}

/// A warning captured during session setup/teardown.
#[derive(Clone, Debug)]
pub(crate) struct WarningEntry {
    pub(crate) context: Arc<str>,
    pub(crate) message: String,
}

/// Outcome counters incremented once per test.
#[derive(Clone, Debug, Default)]
pub(crate) struct OutcomeCounters {
    pub(crate) passed: usize,
    pub(crate) failed: usize,
    pub(crate) errored: usize,
    pub(crate) skipped: usize,
    pub(crate) warned: usize,
    pub(crate) xfailed: usize,
    pub(crate) xpassed: usize,
    pub(crate) xpassed_strict: usize,
    pub(crate) timeout: usize,
    pub(crate) flaky: usize,
    pub(crate) strict_suite: usize,
}

/// Diagnostic data accumulated during a run.
#[derive(Clone, Debug, Default)]
pub(crate) struct DiagnosticBag {
    pub(crate) tip_lines: Vec<TipLine>,
    pub(crate) warning_msgs: Vec<WarningEntry>,
    pub(crate) timings: Vec<TimingEntry>,
}

#[derive(Clone)]
pub(crate) struct RunStats {
    pub(crate) counts: OutcomeCounters,
    pub(crate) diagnostics: DiagnosticBag,
    pub(crate) fixture_cache: Option<FixtureCacheStats>,
    pub(crate) fixture_timings: Vec<FixtureTimingEntry>,
}

/// Return the `n` items with the largest key, sorted descending.
///
/// Uses `select_nth_unstable_by` for O(n) partitioning, then sorts
/// only the top N for deterministic output.
fn top_n_by_key<T: Clone, K: PartialOrd>(items: &[T], n: usize, key: impl Fn(&T) -> K) -> Vec<T> {
    if n == 0 || items.is_empty() {
        return vec![];
    }
    let n = n.min(items.len());
    let mut indices: Vec<usize> = (0..items.len()).collect();
    let cmp = |&a: &usize, &b: &usize| {
        key(&items[b])
            .partial_cmp(&key(&items[a]))
            .unwrap_or(std::cmp::Ordering::Equal)
    };
    indices.select_nth_unstable_by(n - 1, cmp);
    indices.truncate(n);
    indices.sort_unstable_by(cmp);
    indices.iter().map(|&i| items[i].clone()).collect()
}

impl RunStats {
    pub(crate) fn new() -> Self {
        Self {
            counts: OutcomeCounters::default(),
            diagnostics: DiagnosticBag::default(),
            fixture_cache: None,
            fixture_timings: Vec::new(),
        }
    }

    pub(crate) fn record_passed(&mut self, item: &TestItem, no_message_lines: &[usize]) {
        self.counts.passed += 1;
        self.collect_tips(item, no_message_lines);
    }

    pub(crate) fn record_failed(&mut self) {
        self.counts.failed += 1;
    }

    pub(crate) fn record_errored(&mut self) {
        self.counts.errored += 1;
    }

    pub(crate) fn record_timeout(&mut self) {
        self.counts.timeout += 1;
    }

    /// Record a flaky outcome, undoing the original failure count.
    ///
    /// The `original` kind identifies which counter was incremented during
    /// the initial run so we decrement the correct one.
    pub(crate) fn record_flaky(&mut self, original: crate::types::OutcomeKind) {
        self.counts.flaky += 1;
        match original {
            crate::types::OutcomeKind::Failed => {
                self.counts.failed = self.counts.failed.saturating_sub(1);
            }
            crate::types::OutcomeKind::Error => {
                self.counts.errored = self.counts.errored.saturating_sub(1);
            }
            crate::types::OutcomeKind::Timeout => {
                self.counts.timeout = self.counts.timeout.saturating_sub(1);
            }
            _ => {}
        }
    }

    pub(crate) fn record_skipped(&mut self) {
        self.counts.skipped += 1;
    }

    pub(crate) fn record_warned(
        &mut self,
        item: &TestItem,
        reason: &str,
        no_message_lines: &[usize],
    ) {
        self.counts.warned += 1;
        self.diagnostics.warning_msgs.push(WarningEntry {
            context: item.fn_name.clone(),
            message: reason.to_string(),
        });
        self.collect_tips(item, no_message_lines);
    }

    pub(crate) fn record_xfailed(&mut self) {
        self.counts.xfailed += 1;
    }

    /// Single dispatch point — call this from reporters instead of the individual methods.
    pub(crate) fn record(&mut self, item: &TestItem, outcome: &TestOutcome) {
        match outcome {
            TestOutcome::Passed { tips } => {
                self.record_passed(item, tips.as_deref().map_or(&[], |v| v));
            }
            TestOutcome::Failed(..) => self.record_failed(),
            TestOutcome::Error(..) => self.record_errored(),
            TestOutcome::Skipped { .. } => self.record_skipped(),
            TestOutcome::Warned { reason, tips } => {
                self.record_warned(item, reason, tips.as_deref().map_or(&[], |v| v));
            }
            TestOutcome::XFailed { .. } => self.record_xfailed(),
            TestOutcome::XPassed { strict } => self.record_xpassed(*strict),
            TestOutcome::Timeout { .. } => self.record_timeout(),
            TestOutcome::Flaky { original, .. } => self.record_flaky(*original),
        }
    }

    pub(crate) fn record_xpassed(&mut self, strict: bool) {
        self.counts.xpassed += 1;
        if strict {
            self.counts.xpassed_strict += 1;
        }
    }

    pub(crate) fn record_strict_suite(&mut self, count: usize) {
        self.counts.strict_suite += count;
    }

    pub(crate) fn record_timing(&mut self, node_id: &NodeId, duration_ms: DurationMs) {
        self.diagnostics.timings.push(TimingEntry {
            node_id: node_id.clone(),
            duration_ms,
        });
    }

    /// Returns the N slowest tests, sorted by duration descending.
    pub(crate) fn slowest(&self, n: usize) -> Vec<TimingEntry> {
        top_n_by_key(&self.diagnostics.timings, n, |t| t.duration_ms)
    }

    #[cfg(test)]
    pub(crate) fn record_fixture_timing(&mut self, entry: FixtureTimingEntry) {
        self.fixture_timings.push(entry);
    }

    /// Returns the N slowest fixtures, sorted by total time (setup + teardown) descending.
    pub(crate) fn slowest_fixtures(&self, n: usize) -> Vec<FixtureTimingEntry> {
        top_n_by_key(&self.fixture_timings, n, |t| t.total())
    }

    fn collect_tips(&mut self, item: &TestItem, lines: &[usize]) {
        if !lines.is_empty() {
            let file = item.module_path.clone();
            for &ln in lines {
                self.diagnostics.tip_lines.push(TipLine {
                    file: file.clone(),
                    lineno: crate::types::LineNo::new(ln),
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_record_flaky_increments_counter() {
        let mut stats = RunStats::new();
        stats.record_flaky(crate::types::OutcomeKind::Failed);
        assert_eq!(stats.counts.flaky, 1);
        assert_eq!(stats.counts.failed, 0);
    }

    /// When a timed-out test passes on retry, the timeout counter must be decremented —
    /// not the failed counter. This was a real bug before #936: the old priority heuristic
    /// would decrement `failed` if it was non-zero, even when the original was a timeout.
    #[test]
    fn record_flaky_decrements_correct_counter_for_timeout() {
        let mut stats = RunStats::new();
        stats.record_failed(); // unrelated failure from another test
        stats.record_timeout(); // THIS test timed out, then passed on retry
        stats.record_flaky(crate::types::OutcomeKind::Timeout);
        // Timeout decremented, failed left alone
        assert_eq!(stats.counts.timeout, 0);
        assert_eq!(stats.counts.failed, 1); // must NOT be decremented
        assert_eq!(stats.counts.flaky, 1);
    }

    /// Same scenario for Error — an errored test passes on retry, with a concurrent failure.
    #[test]
    fn record_flaky_decrements_correct_counter_for_error() {
        let mut stats = RunStats::new();
        stats.record_failed(); // unrelated failure
        stats.record_errored(); // THIS test errored, then passed on retry
        stats.record_flaky(crate::types::OutcomeKind::Error);
        assert_eq!(stats.counts.errored, 0);
        assert_eq!(stats.counts.failed, 1); // untouched
        assert_eq!(stats.counts.flaky, 1);
    }

    #[test]
    fn test_record_timeout_increments_counter() {
        let mut stats = RunStats::new();
        stats.record_timeout();
        assert_eq!(stats.counts.timeout, 1);
        assert_eq!(stats.counts.errored, 0); // must NOT increment errored
    }

    #[test]
    fn test_record_timing_accumulates_entries() {
        let mut stats = RunStats::new();
        stats.record_timing(
            &NodeId::from_raw("tests/test_foo.py::test_a"),
            DurationMs::new(42.5),
        );
        stats.record_timing(
            &NodeId::from_raw("tests/test_foo.py::test_b"),
            DurationMs::new(10.0),
        );
        assert_eq!(stats.diagnostics.timings.len(), 2);
    }

    #[test]
    fn test_slowest_returns_sorted_descending() {
        let mut stats = RunStats::new();
        stats.record_timing(&NodeId::from_raw("test_fast"), DurationMs::new(5.0));
        stats.record_timing(&NodeId::from_raw("test_slow"), DurationMs::new(200.0));
        stats.record_timing(&NodeId::from_raw("test_medium"), DurationMs::new(50.0));
        let top2 = stats.slowest(2);
        assert_eq!(top2[0].node_id.as_ref(), "test_slow");
        assert_eq!(top2[1].node_id.as_ref(), "test_medium");
    }

    #[test]
    fn test_slowest_n_greater_than_count_returns_all() {
        let mut stats = RunStats::new();
        stats.record_timing(&NodeId::from_raw("test_a"), DurationMs::new(10.0));
        let top10 = stats.slowest(10);
        assert_eq!(top10.len(), 1);
    }

    #[test]
    fn test_slowest_zero_returns_empty() {
        let mut stats = RunStats::new();
        stats.record_timing(&NodeId::from_raw("test_a"), DurationMs::new(10.0));
        assert!(stats.slowest(0).is_empty());
    }

    #[test]
    fn test_record_strict_suite_increments_counter() {
        let mut stats = RunStats::new();
        stats.record_strict_suite(3);
        assert_eq!(stats.counts.strict_suite, 3);
    }

    #[test]
    fn test_record_strict_suite_accumulates_on_repeated_calls() {
        let mut stats = RunStats::new();
        stats.record_strict_suite(3);
        stats.record_strict_suite(2);
        assert_eq!(stats.counts.strict_suite, 5);
    }

    #[test]
    fn test_slowest_fixtures_returns_sorted_descending() {
        let mut stats = RunStats::new();
        stats.record_fixture_timing(FixtureTimingEntry {
            name: "fast_fx".into(),
            total_setup: DurationMs::new(5.0),
            setup_count: 1,
            total_teardown: DurationMs::new(0.0),
            teardown_count: 0,
        });
        stats.record_fixture_timing(FixtureTimingEntry {
            name: "slow_fx".into(),
            total_setup: DurationMs::new(200.0),
            setup_count: 1,
            total_teardown: DurationMs::new(50.0),
            teardown_count: 1,
        });
        let top = stats.slowest_fixtures(2);
        assert_eq!(top[0].name, "slow_fx");
        assert_eq!(top[1].name, "fast_fx");
    }

    #[test]
    fn test_slowest_fixtures_zero_returns_empty() {
        let mut stats = RunStats::new();
        stats.record_fixture_timing(FixtureTimingEntry {
            name: "fx".into(),
            total_setup: DurationMs::new(10.0),
            setup_count: 1,
            total_teardown: DurationMs::new(0.0),
            teardown_count: 0,
        });
        assert!(stats.slowest_fixtures(0).is_empty());
    }

    #[test]
    fn fixture_cache_summary_zero_total_does_not_panic() {
        let stats = FixtureCacheStats {
            hits: 0,
            misses: 0,
            breakdown: vec![],
        };
        let s = stats.summary();
        assert!(!s.is_empty());
    }

    #[test]
    fn test_slowest_fixtures_n_greater_than_count_returns_all() {
        let mut stats = RunStats::new();
        stats.record_fixture_timing(FixtureTimingEntry {
            name: "fx".into(),
            total_setup: DurationMs::new(10.0),
            setup_count: 1,
            total_teardown: DurationMs::new(0.0),
            teardown_count: 0,
        });
        let top = stats.slowest_fixtures(10);
        assert_eq!(top.len(), 1);
    }
}
