//! Shared print helpers for reporter summary output.

use std::io::{self, Write};

use crate::types::{CollectError, ExitCode};

use super::colors;
use super::exit::compute_exit_code;
use super::format::{fmt_summary, fmt_tip_block, fmt_warning_block};
use super::stats::RunStats;
use super::{sep_width, ReporterOpts};

pub(crate) fn print_collected(total: usize, fn_count: usize, async_count: usize) {
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
        println!(
            "collected {} item{}{} ({} async)\n",
            total, suffix, from_fns, async_count
        );
    } else {
        println!("collected {} item{}{}\n", total, suffix, from_fns);
    }
}

pub(crate) fn print_summary_section(
    stats: &RunStats,
    opts: &ReporterOpts,
    collect_errors: &[CollectError],
    interrupted: bool,
) -> ExitCode {
    let tip_block = fmt_tip_block(&stats.diagnostics.tip_lines, opts.show_tips, opts.use_color);
    let warn_block = fmt_warning_block(
        &stats.diagnostics.warning_msgs,
        opts.show_warnings,
        opts.use_color,
    );
    let summary = fmt_summary(stats, collect_errors.len(), opts.use_color);
    println!("\n{}", summary);
    if let Some(n) = opts.show_durations {
        let slowest = stats.slowest(n);
        if !slowest.is_empty() {
            println!(
                "\n{}",
                colors::color_dim(&format!("slowest {} tests", slowest.len()), opts.use_color)
            );
            for entry in &slowest {
                println!("  {:>8.2}ms  {}", entry.duration_ms, entry.node_id);
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
                        entry.total_setup_ms,
                        entry.setup_count,
                        entry.total_teardown_ms,
                        entry.teardown_count,
                    )
                } else {
                    format!(
                        "setup {:.2}ms ({})",
                        entry.total_setup_ms, entry.setup_count
                    )
                };
                println!(
                    "  {:>8.2}ms  {} \u{2014} {}",
                    entry.total_ms(),
                    entry.name,
                    detail,
                );
            }
        }
    }
    // Fixture cache stats — always shown when shared fixtures were used.
    if let Some(summary) = stats.fixture_cache_summary() {
        println!("\n{}", colors::color_dim(&summary, opts.use_color));
        if opts.verbosity >= crate::config::Verbosity::Detailed {
            let mut entries = stats.fixture_cache_breakdown.clone();
            entries.sort_by_key(|e| std::cmp::Reverse(e.hits + e.misses));
            for e in &entries {
                let total = e.hits + e.misses;
                let pct = (100 * e.hits).checked_div(total).unwrap_or(0);
                println!("    {:<14} {}/{} ({}%)", e.name, e.hits, total, pct);
            }
        }
    }
    if !tip_block.is_empty() {
        print!("{}", tip_block);
    }
    if !warn_block.is_empty() {
        print!("{}", warn_block);
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

pub(crate) fn flush() {
    let _ = io::stdout().flush();
}

pub(crate) fn print_collect_errors(collect_errors: &[CollectError], use_color: bool) {
    if !collect_errors.is_empty() {
        println!("\nCOLLECTION ERRORS");
        println!("{}", colors::color_dim(&"═".repeat(sep_width()), use_color));
        let last = collect_errors.len() - 1;
        for (i, ce) in collect_errors.iter().enumerate() {
            println!("{}", ce);
            if i < last {
                println!();
            }
        }
    }
}

pub(crate) fn print_strict_suite_section(opts: &ReporterOpts) {
    if !opts.strict_suite_lines.is_empty() {
        let hdr = format!(
            "STRICT {}",
            colors::color_dim(
                &"═".repeat(sep_width().saturating_sub("STRICT ".len())),
                opts.use_color,
            )
        );
        println!("\n{}", hdr);
        for line in &opts.strict_suite_lines {
            println!("  {}", line);
        }
    }
}

pub(crate) fn print_strict_abort(formatted_lines: &[String], use_color: bool) {
    println!("\nSTRICT VIOLATIONS");
    println!("{}", colors::color_dim(&"═".repeat(sep_width()), use_color));
    for line in formatted_lines {
        println!("  {}", line);
    }
    println!("strict violations found — aborting (exit 3)");
}
