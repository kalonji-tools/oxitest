use crate::types::{TestItem, TestOutcome};

pub(crate) struct RunStats {
    pub(crate) passed: usize,
    pub(crate) failed: usize,
    pub(crate) errored: usize,
    pub(crate) skipped: usize,
    pub(crate) warned: usize,
    pub(crate) xfailed: usize,
    pub(crate) xpassed: usize,
    pub(crate) xpassed_strict: usize,
    pub(crate) timeout: usize,
    pub(crate) strict_suite: usize,
    pub(crate) tip_lines: Vec<(String, usize)>,
    pub(crate) warning_msgs: Vec<(String, String)>,
    pub(crate) timings: Vec<(String, f64)>,
}

impl RunStats {
    pub(crate) fn new() -> Self {
        Self {
            passed: 0,
            failed: 0,
            errored: 0,
            skipped: 0,
            warned: 0,
            xfailed: 0,
            xpassed: 0,
            xpassed_strict: 0,
            timeout: 0,
            strict_suite: 0,
            tip_lines: Vec::new(),
            warning_msgs: Vec::new(),
            timings: Vec::new(),
        }
    }

    pub(crate) fn record_passed(&mut self, item: &TestItem, no_message_lines: &[usize]) {
        self.passed += 1;
        self.collect_tips(item, no_message_lines);
    }

    pub(crate) fn record_failed(&mut self) {
        self.failed += 1;
    }

    pub(crate) fn record_errored(&mut self) {
        self.errored += 1;
    }

    pub(crate) fn record_timeout(&mut self) {
        self.timeout += 1;
    }

    pub(crate) fn record_skipped(&mut self) {
        self.skipped += 1;
    }

    pub(crate) fn record_warned(
        &mut self,
        item: &TestItem,
        reason: &str,
        no_message_lines: &[usize],
    ) {
        self.warned += 1;
        self.warning_msgs
            .push((item.node_id.to_string(), reason.to_string()));
        self.collect_tips(item, no_message_lines);
    }

    pub(crate) fn record_xfailed(&mut self) {
        self.xfailed += 1;
    }

    /// Single dispatch point — call this from reporters instead of the individual methods.
    pub(crate) fn record(&mut self, item: &TestItem, outcome: &TestOutcome) {
        match outcome {
            TestOutcome::Passed { no_message_lines } => self.record_passed(item, no_message_lines),
            TestOutcome::Failed { .. } => self.record_failed(),
            TestOutcome::Error { .. } => self.record_errored(),
            TestOutcome::Skipped { .. } => self.record_skipped(),
            TestOutcome::Warned {
                reason,
                no_message_lines,
            } => {
                self.record_warned(item, reason, no_message_lines);
            }
            TestOutcome::XFailed { .. } => self.record_xfailed(),
            TestOutcome::XPassed { strict } => self.record_xpassed(*strict),
            TestOutcome::Timeout { .. } => self.record_timeout(),
        }
    }

    pub(crate) fn record_xpassed(&mut self, strict: bool) {
        self.xpassed += 1;
        if strict {
            self.xpassed_strict += 1;
        }
    }

    pub(crate) fn record_strict_suite(&mut self, count: usize) {
        self.strict_suite += count;
    }

    pub(crate) fn record_timing(&mut self, node_id: &str, duration_ms: f64) {
        self.timings.push((node_id.to_string(), duration_ms));
    }

    /// Returns the N slowest tests, sorted by duration descending.
    pub(crate) fn slowest(&self, n: usize) -> Vec<(String, f64)> {
        if n == 0 {
            return vec![];
        }
        let mut sorted = self.timings.clone();
        sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        sorted.truncate(n);
        sorted
    }

    fn collect_tips(&mut self, item: &TestItem, lines: &[usize]) {
        if !lines.is_empty() {
            let file = item.module_path.to_string();
            for &ln in lines {
                self.tip_lines.push((file.clone(), ln));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_record_timeout_increments_counter() {
        let mut stats = RunStats::new();
        stats.record_timeout();
        assert_eq!(stats.timeout, 1);
        assert_eq!(stats.errored, 0); // must NOT increment errored
    }

    #[test]
    fn test_record_timing_accumulates_entries() {
        let mut stats = RunStats::new();
        stats.record_timing("tests/test_foo.py::test_a", 42.5);
        stats.record_timing("tests/test_foo.py::test_b", 10.0);
        assert_eq!(stats.timings.len(), 2);
    }

    #[test]
    fn test_slowest_returns_sorted_descending() {
        let mut stats = RunStats::new();
        stats.record_timing("test_fast", 5.0);
        stats.record_timing("test_slow", 200.0);
        stats.record_timing("test_medium", 50.0);
        let top2 = stats.slowest(2);
        assert_eq!(top2[0].0, "test_slow");
        assert_eq!(top2[1].0, "test_medium");
    }

    #[test]
    fn test_slowest_n_greater_than_count_returns_all() {
        let mut stats = RunStats::new();
        stats.record_timing("test_a", 10.0);
        let top10 = stats.slowest(10);
        assert_eq!(top10.len(), 1);
    }

    #[test]
    fn test_slowest_zero_returns_empty() {
        let mut stats = RunStats::new();
        stats.record_timing("test_a", 10.0);
        assert!(stats.slowest(0).is_empty());
    }

    #[test]
    fn test_record_strict_suite_increments_counter() {
        let mut stats = RunStats::new();
        stats.record_strict_suite(3);
        assert_eq!(stats.strict_suite, 3);
    }

    #[test]
    fn test_record_strict_suite_accumulates_on_repeated_calls() {
        let mut stats = RunStats::new();
        stats.record_strict_suite(3);
        stats.record_strict_suite(2);
        assert_eq!(stats.strict_suite, 5);
    }
}
