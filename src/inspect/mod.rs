//! Interactive TUI for test suite introspection.
//!
//! This module implements `oxitest inspect`, a ratatui-based terminal UI that
//! lets users browse tests, fixtures, marks, and other collected metadata.

mod app;
pub(crate) mod graph;
mod input;
pub(crate) mod search;
mod ui;

use crate::config::{self, cli::InspectArgs};
use graph::InspectGraph;

/// Build the inspect graph from instant-tier data available without a
/// Python session.
///
/// This collects test, mark, and helper entries via Rust AST extraction
/// and wires up edges.  Fixture and plugin nodes require a Python session
/// and are added later by progressive loading (#1119).
fn build_graph(
    _args: &InspectArgs,
    cfg: &config::Config,
) -> Result<InspectGraph, Box<dyn std::error::Error>> {
    use crate::collector;
    use crate::query::extract;
    use graph::builder::GraphBuilder;

    let (test_files, conftest_files) = collector::collect_files(cfg)?;

    let mut builder = GraphBuilder::new();
    builder.add_test_entries(&extract::extract_test_entries(&test_files));
    builder.add_mark_entries(&extract::extract_mark_entries(
        &test_files,
        &cfg.markers.registered_markers,
    ));
    builder.add_helper_entries(&extract::extract_helper_entries(&conftest_files));
    builder.resolve_edges();

    Ok(builder.build())
}

/// Launch the inspect TUI.
///
/// Builds the inspect graph from instant-tier data, then sets up the terminal
/// (raw mode, alternate screen, mouse capture), runs the event loop, and
/// restores the terminal on exit.
pub(crate) fn run(
    args: &InspectArgs,
    cfg: &config::Config,
) -> Result<(), Box<dyn std::error::Error>> {
    let graph = build_graph(args, cfg)?;
    let mut terminal = ui::setup_terminal()?;
    let result = app::InspectApp::new(Some(graph)).run(&mut terminal);
    ui::restore_terminal(&mut terminal)?;
    result
}
