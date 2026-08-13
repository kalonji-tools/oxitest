//! Shared print helpers for reporter summary output.

use std::io::{self, Write};

use crate::types::{CollectError, ExitCode};

use super::exit::compute_exit_code;
use super::format::{fmt_diagnostics_block, fmt_summary, fmt_tip_block};
use super::stats::RunStats;
use super::{ReporterOpts, sep_width};
use crate::colors;

/// Print the start-of-run header: the rootdir, then the collected count.
///
/// The rootdir is announced because every path inside a diagnostic is shown
/// against it (#1851), and a relative path is not resolvable on its own. It is
/// printed here rather than gated on whether a diagnostic follows, because the
/// reporter cannot know that yet — and because a header that appears only on
/// some runs is a worse contract than one that always does.
///
/// `None` means the run has no project, which is the test builder's default.
pub fn print_collected(
    total: usize,
    fn_count: usize,
    async_count: usize,
    rootdir: Option<&camino::Utf8Path>,
) {
    if let Some(root) = rootdir {
        println!("rootdir: {root}");
    }
    let suffix = if total == 1 { "" } else { "s" };
    let from_fns = if fn_count > 0 && fn_count < total {
        format!(
            " from {} function{}",
            fn_count,
            if fn_count == 1 { "" } else { "s" }
        )
    } else {
        String::new()
    };
    if async_count > 0 {
        println!("collected {total} item{suffix}{from_fns} ({async_count} async)\n");
    } else {
        println!("collected {total} item{suffix}{from_fns}\n");
    }
}

pub fn print_summary_section(
    stats: &RunStats,
    opts: &ReporterOpts,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> ExitCode {
    let tip_block = fmt_tip_block(&stats.diagnostics.tip_lines, opts.show_tips, opts.use_color);
    let warn_block = fmt_diagnostics_block(
        &stats.diagnostics.entries,
        opts.show_warnings,
        opts.use_color,
    );
    let summary = fmt_summary(stats, collect_errors.len(), opts.use_color);
    println!("\n{summary}");
    if let Some(n) = opts.show_durations {
        let slowest = stats.slowest(n);
        if !slowest.is_empty() {
            println!(
                "\n{}",
                colors::color_dim(&format!("slowest {} tests", slowest.len()), opts.use_color)
            );
            for entry in &slowest {
                println!("  {:>8.2}ms  {}", entry.duration_ms.as_f64(), entry.node_id);
            }
        }
        let slowest_fx = stats.slowest_fixtures(n);
        if !slowest_fx.is_empty() {
            println!(
                "\n{}",
                colors::color_dim(
                    &format!("slowest {} fixtures", slowest_fx.len()),
                    opts.use_color,
                )
            );
            for entry in &slowest_fx {
                let detail = if entry.teardown_count > 0 {
                    format!(
                        "setup {:.2}ms ({}) + teardown {:.2}ms ({})",
                        entry.total_setup.as_f64(),
                        entry.setup_count,
                        entry.total_teardown.as_f64(),
                        entry.teardown_count,
                    )
                } else {
                    format!(
                        "setup {:.2}ms ({})",
                        entry.total_setup.as_f64(),
                        entry.setup_count
                    )
                };
                println!(
                    "  {:>8.2}ms  {} \u{2014} {}",
                    entry.total().as_f64(),
                    entry.name,
                    detail,
                );
            }
        }
    }
    // Fixture cache stats — always shown when shared fixtures were used.
    if let Some(cache) = &stats.fixture_cache {
        println!("\n{}", colors::color_dim(&cache.summary(), opts.use_color));
        if opts.verbosity >= crate::config::Verbosity::Detailed {
            let mut entries = cache.breakdown.clone();
            entries.sort_by_key(|e| std::cmp::Reverse(e.hits + e.misses));
            for e in &entries {
                let total = e.hits + e.misses;
                let pct = (100 * e.hits).checked_div(total).unwrap_or(0);
                println!("    {:<14} {}/{} ({}%)", e.name, e.hits, total, pct);
            }
        }
    }
    if !tip_block.is_empty() {
        print!("{tip_block}");
    }
    if !warn_block.is_empty() {
        print!("{warn_block}");
    }
    if !tip_block.is_empty() || !warn_block.is_empty() {
        println!(
            "{}",
            colors::color_dim(&"═".repeat(sep_width()), opts.use_color)
        );
    }
    flush();
    compute_exit_code(stats, collect_errors.len(), interrupted)
}

pub fn flush() {
    let _ = io::stdout().flush();
}

/// Render the collection-error block, or the empty string when there is none.
///
/// Split out of [`print_collect_errors`] so a test can read what that function
/// writes. `println!` goes to a stdout no Rust test can capture, so the two
/// tests over it could only assert that it did not panic — which is true of a
/// body that prints nothing at all (#2112). This is the same `fmt_` / `print_`
/// split the rest of the reporter already uses: [`fmt_summary`],
/// [`fmt_tip_block`] and [`fmt_diagnostics_block`] each return a `String` that
/// [`print_summary_section`] prints.
///
/// Empty in, empty out — the caller prints nothing rather than a bare newline.
pub fn fmt_collect_errors(collect_errors: &[CollectError], use_color: bool) -> String {
    if collect_errors.is_empty() {
        return String::new();
    }
    let sep = colors::color_dim(&"═".repeat(sep_width()), use_color);
    let body = collect_errors
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n\n");
    format!("\nCOLLECTION ERRORS\n{sep}\n{body}")
}

pub fn print_collect_errors(collect_errors: &[CollectError], use_color: bool) {
    let block = fmt_collect_errors(collect_errors, use_color);
    if !block.is_empty() {
        println!("{block}");
    }
}

pub fn print_strict_suite_section(opts: &ReporterOpts) {
    if !opts.strict_suite_lines.is_empty() {
        let hdr = format!(
            "STRICT {}",
            colors::color_dim(
                &"═".repeat(sep_width().saturating_sub("STRICT ".len())),
                opts.use_color,
            )
        );
        println!("\n{hdr}");
        for line in &opts.strict_suite_lines {
            println!("  {line}");
        }
    }
}

pub fn print_strict_abort(formatted_lines: &[String], use_color: bool) {
    println!("\nSTRICT VIOLATIONS");
    println!("{}", colors::color_dim(&"═".repeat(sep_width()), use_color));
    for line in formatted_lines {
        println!("  {line}");
    }
    println!("strict violations found — aborting (exit 3)");
}
