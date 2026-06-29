//! Interactive TUI for test suite introspection.
//!
//! This module implements `oxitest inspect`, a ratatui-based terminal UI that
//! lets users browse tests, fixtures, marks, and other collected metadata.

mod app;
mod input;
pub(crate) mod search;
mod ui;

use crate::config::{self, cli::InspectArgs};

/// Launch the inspect TUI.
///
/// Sets up the terminal (raw mode, alternate screen, mouse capture), runs the
/// event loop, and restores the terminal on exit.
pub(crate) fn run(
    _args: &InspectArgs,
    _cfg: &config::Config,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut terminal = ui::setup_terminal()?;
    let result = app::InspectApp::new().run(&mut terminal);
    ui::restore_terminal(&mut terminal)?;
    result
}
