use std::sync::Arc;

use crate::types::{DurationMs, NodeId, OutcomeKind, TestItem, TestOutcome};

/// Per-fixture cache hit/miss entry.
#[derive(Clone, Debug)]
pub struct FixtureCacheEntry {
    pub(crate) name: String,
    pub(crate) hits: usize,
    pub(crate) misses: usize,
}

/// Aggregate fixture cache statistics returned by the bridge.
#[derive(Clone, Debug)]
pub struct FixtureCacheStats {
    pub(crate) hits: usize,
    pub(crate) misses: usize,
    pub(crate) breakdown: Vec<FixtureCacheEntry>,
}

impl FixtureCacheStats {
    /// Format the fixture cache summary line.
    ///
    /// Labelled "fixture cache", not "shared fixture cache": these numbers
    /// cover every cached tier, not one of them.
    pub(crate) fn summary(&self) -> String {
        let total = self.hits + self.misses;
        if total == 0 {
            return "fixture cache: no fixtures used".to_string();
        }
        let pct = 100 * self.hits / total;
        format!("fixture cache: {}/{} hits ({}%)", self.hits, total, pct)
    }
}

/// Per-fixture timing aggregate returned by the Python bridge.
#[derive(Clone, Debug)]
pub struct FixtureTimingEntry {
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
pub struct TimingEntry {
    pub(crate) node_id: NodeId,
    pub(crate) duration_ms: DurationMs,
}

/// A `print()` call inside a passing test (potential debugging leftover).
#[derive(Clone, Debug)]
pub struct TipLine {
    pub(crate) file: camino::Utf8PathBuf,
    pub(crate) lineno: crate::types::LineNo,
}

/// Severity of a diagnostic message from the Python bridge.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum DiagnosticSeverity {
    Error,
    Warning,
    Notice,
}

impl DiagnosticSeverity {
    /// Map a wire severity string onto a severity.
    ///
    /// Shared by the PyO3 path and the worker LDJSON path so the two cannot
    /// drift. An unknown string becomes a notice rather than an error: a newer
    /// worker inventing a severity should not be reported as a failure.
    pub(crate) fn from_wire(severity: &str) -> Self {
        match severity {
            "error" => Self::Error,
            "warning" => Self::Warning,
            _ => Self::Notice,
        }
    }
}

/// A diagnostic message emitted by the Python bridge or Rust pipeline.
///
/// Replaces `WarningEntry` — carries severity for differentiated rendering.
#[derive(Clone, Debug)]
pub struct DiagnosticEntry {
    pub(crate) severity: DiagnosticSeverity,
    pub(crate) context: Arc<str>,
    pub(crate) message: String,
    pub(crate) file: Option<camino::Utf8PathBuf>,
    pub(crate) lineno: Option<crate::types::LineNo>,
}

impl DiagnosticEntry {
    /// Build an entry from the wire fields the PyO3 and LDJSON paths share.
    ///
    /// Both carry `file`/`lineno` as an empty string and a zero rather than as
    /// options, so both need the same "absent means empty" decoding. Keeping it
    /// here means the two paths cannot disagree about what absent looks like.
    pub(crate) fn from_wire(
        severity: &str,
        context: &str,
        message: String,
        file: String,
        lineno: u32,
    ) -> Self {
        Self {
            severity: DiagnosticSeverity::from_wire(severity),
            context: Arc::from(context),
            message,
            file: (!file.is_empty()).then(|| camino::Utf8PathBuf::from(file)),
            lineno: (lineno != 0).then(|| crate::types::LineNo::new(lineno as usize)),
        }
    }
}

/// Outcome counters indexed by [`OutcomeKind`].
#[derive(Clone, Debug)]
pub struct OutcomeCounters {
    pub(crate) by_kind: [usize; OutcomeKind::COUNT],
}

impl Default for OutcomeCounters {
    fn default() -> Self {
        Self {
            by_kind: [0; OutcomeKind::COUNT],
        }
    }
}

/// Strict-mode counters — separate from outcome counts because these
/// are not test outcomes (`xpassed_strict` is a sub-count, `suite_violations`
/// are collection-time violations).
#[derive(Clone, Debug, Default)]
pub struct StrictCounts {
    /// Subset of `XPassed` where the xfail mark was strict.
    pub(crate) xpassed_strict: usize,
    /// Strict-mode suite violations (not test outcomes).
    pub(crate) suite_violations: usize,
}

impl OutcomeCounters {
    pub(crate) const fn get(&self, kind: OutcomeKind) -> usize {
        self.by_kind[kind as usize]
    }

    const fn increment(&mut self, kind: OutcomeKind) {
        self.by_kind[kind as usize] += 1;
    }

    const fn decrement(&mut self, kind: OutcomeKind) {
        self.by_kind[kind as usize] = self.by_kind[kind as usize].saturating_sub(1);
    }
}

/// Diagnostic data accumulated during a run.
#[derive(Clone, Debug, Default)]
pub struct DiagnosticBag {
    pub(crate) tip_lines: Vec<TipLine>,
    pub(crate) entries: Vec<DiagnosticEntry>,
}

#[derive(Clone)]
pub struct RunStats {
    pub(crate) counts: OutcomeCounters,
    pub(crate) strict: StrictCounts,
    pub(crate) diagnostics: DiagnosticBag,
    pub(crate) timings: Vec<TimingEntry>,
    pub(crate) fixture_cache: Option<FixtureCacheStats>,
    pub(crate) fixture_timings: Vec<FixtureTimingEntry>,
    /// Tests that errored because the suite is wired wrong, rather than because
    /// an assertion failed.
    ///
    /// A non-empty set makes the run exit
    /// [`ExitCode::UsageError`](crate::types::ExitCode::UsageError) (#1761).
    ///
    /// Node ids rather than a flag or a count, because a retry that passes must
    /// clear **only its own** violation. `record_flaky` also clears the ordinary
    /// failure counters, but those are per-kind totals, so clearing them needs
    /// no identity. This does: a run can hold one test's wiring error and a
    /// different test's ordinary error, and if the second goes flaky it must not
    /// cancel the first. A flag cannot express that, and a count would decrement
    /// the wrong violation.
    pub(crate) usage_error_ids: std::collections::HashSet<NodeId>,
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
            strict: StrictCounts::default(),
            diagnostics: DiagnosticBag::default(),
            timings: Vec::new(),
            fixture_cache: None,
            fixture_timings: Vec::new(),
            usage_error_ids: std::collections::HashSet::new(),
        }
    }

    pub(crate) fn record_passed(&mut self, item: &TestItem, no_message_lines: &[usize]) {
        self.counts.increment(OutcomeKind::Passed);
        self.collect_tips(item, no_message_lines);
    }

    pub(crate) const fn record_failed(&mut self) {
        self.counts.increment(OutcomeKind::Failed);
    }

    pub(crate) const fn record_errored(&mut self) {
        self.counts.increment(OutcomeKind::Error);
    }

    pub(crate) const fn record_timeout(&mut self) {
        self.counts.increment(OutcomeKind::Timeout);
    }

    /// Record a flaky outcome, undoing the original failure count.
    ///
    /// The `original` kind identifies which counter was incremented during
    /// the initial run so we decrement the correct one.
    ///
    /// `node_id` clears this test's usage-error record, if it had one. Only its
    /// own: another test's wiring error must survive this one passing (#1761).
    pub(crate) fn record_flaky(&mut self, node_id: &NodeId, original: OutcomeKind) {
        self.counts.increment(OutcomeKind::Flaky);
        match original {
            OutcomeKind::Failed | OutcomeKind::Error | OutcomeKind::Timeout => {
                self.counts.decrement(original);
            }
            _ => {}
        }
        self.usage_error_ids.remove(node_id);
    }

    pub(crate) const fn record_skipped(&mut self) {
        self.counts.increment(OutcomeKind::Skipped);
    }

    pub(crate) fn record_warned(
        &mut self,
        item: &TestItem,
        reason: &str,
        no_message_lines: &[usize],
    ) {
        self.counts.increment(OutcomeKind::Warned);
        self.diagnostics.entries.push(DiagnosticEntry {
            severity: DiagnosticSeverity::Warning,
            context: item.fn_name.clone(),
            message: reason.to_string(),
            file: None,
            lineno: None,
        });
        self.collect_tips(item, no_message_lines);
    }

    pub(crate) const fn record_xfailed(&mut self) {
        self.counts.increment(OutcomeKind::XFailed);
    }

    /// Single dispatch point — call this from reporters instead of the individual methods.
    pub(crate) fn record(&mut self, item: &TestItem, outcome: &TestOutcome) {
        match outcome {
            TestOutcome::Passed { tips } => {
                self.record_passed(item, tips.as_deref().map_or(&[], |v| v));
            }
            TestOutcome::Failed(..) => self.record_failed(),
            TestOutcome::Error(diagnostic) => {
                self.record_errored();
                // Recorded, not short-circuited: the run continues and every
                // remaining test still reports (#1761).
                if diagnostic.usage_error {
                    self.usage_error_ids.insert(item.node_id.clone());
                }
            }
            TestOutcome::Skipped { .. } => self.record_skipped(),
            TestOutcome::Warned { reason, tips } => {
                self.record_warned(item, reason, tips.as_deref().map_or(&[], |v| v));
            }
            TestOutcome::XFailed { .. } => self.record_xfailed(),
            TestOutcome::XPassed { strict } => self.record_xpassed(*strict),
            TestOutcome::Timeout { .. } => self.record_timeout(),
            TestOutcome::Flaky { original, .. } => {
                self.record_flaky(&item.node_id, *original);
            }
        }
    }

    pub(crate) const fn record_xpassed(&mut self, strict: bool) {
        self.counts.increment(OutcomeKind::XPassed);
        if strict {
            self.strict.xpassed_strict += 1;
        }
    }

    pub(crate) const fn record_strict_suite(&mut self, count: usize) {
        self.strict.suite_violations += count;
    }

    pub(crate) fn record_timing(&mut self, node_id: &NodeId, duration_ms: DurationMs) {
        self.timings.push(TimingEntry {
            node_id: node_id.clone(),
            duration_ms,
        });
    }

    /// Returns the N slowest tests, sorted by duration descending.
    pub(crate) fn slowest(&self, n: usize) -> Vec<TimingEntry> {
        top_n_by_key(&self.timings, n, |t| t.duration_ms)
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
            let file = camino::Utf8PathBuf::from(item.module_path());
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
        stats.record_flaky(&NodeId::from_raw("t.py::test_a"), OutcomeKind::Failed);
        assert_eq!(stats.counts.get(OutcomeKind::Flaky), 1);
        assert_eq!(stats.counts.get(OutcomeKind::Failed), 0);
    }

    /// When a timed-out test passes on retry, the timeout counter must be decremented —
    /// not the failed counter. This was a real bug before #936: the old priority heuristic
    /// would decrement `failed` if it was non-zero, even when the original was a timeout.
    #[test]
    fn record_flaky_decrements_correct_counter_for_timeout() {
        let mut stats = RunStats::new();
        stats.record_failed(); // unrelated failure from another test
        stats.record_timeout(); // THIS test timed out, then passed on retry
        stats.record_flaky(&NodeId::from_raw("t.py::test_a"), OutcomeKind::Timeout);
        // Timeout decremented, failed left alone
        assert_eq!(stats.counts.get(OutcomeKind::Timeout), 0);
        assert_eq!(stats.counts.get(OutcomeKind::Failed), 1); // must NOT be decremented
        assert_eq!(stats.counts.get(OutcomeKind::Flaky), 1);
    }

    /// Same scenario for Error — an errored test passes on retry, with a concurrent failure.
    #[test]
    fn record_flaky_decrements_correct_counter_for_error() {
        let mut stats = RunStats::new();
        stats.record_failed(); // unrelated failure
        stats.record_errored(); // THIS test errored, then passed on retry
        stats.record_flaky(&NodeId::from_raw("t.py::test_a"), OutcomeKind::Error);
        assert_eq!(stats.counts.get(OutcomeKind::Error), 0);
        assert_eq!(stats.counts.get(OutcomeKind::Failed), 1); // untouched
        assert_eq!(stats.counts.get(OutcomeKind::Flaky), 1);
    }

    #[test]
    fn test_record_timeout_increments_counter() {
        let mut stats = RunStats::new();
        stats.record_timeout();
        assert_eq!(stats.counts.get(OutcomeKind::Timeout), 1);
        assert_eq!(stats.counts.get(OutcomeKind::Error), 0); // must NOT increment errored
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
        assert_eq!(stats.timings.len(), 2);
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
        assert_eq!(stats.strict.suite_violations, 3);
    }

    #[test]
    fn test_record_strict_suite_accumulates_on_repeated_calls() {
        let mut stats = RunStats::new();
        stats.record_strict_suite(3);
        stats.record_strict_suite(2);
        assert_eq!(stats.strict.suite_violations, 5);
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
        assert_eq!(
            s, "fixture cache: no fixtures used",
            "the zero case must divide by nothing and must not say 'shared' — \
             these numbers cover every cached tier, not one of them"
        );
    }

    #[test]
    fn fixture_cache_summary_reports_hits_over_total() {
        let stats = FixtureCacheStats {
            hits: 2,
            misses: 1,
            breakdown: vec![],
        };
        let s = stats.summary();
        assert_eq!(
            s, "fixture cache: 2/3 hits (66%)",
            "the denominator is hits+misses, not misses alone, and the label \
             must stay tier-neutral — module-lifetime fixtures land here too"
        );
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
