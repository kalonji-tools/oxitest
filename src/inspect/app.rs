//! Application state and event loop for `oxitest inspect`.

use crossterm::event::{self, Event};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;

use super::input;
use super::ui;

// ── InputMode ────────────────────────────────────────────────────────────────

/// Current input mode of the TUI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum InputMode {
    /// Normal navigation mode.
    Normal,
    /// Search mode — keystrokes append to the query string.
    Search { query: String },
}

// ── InspectApp ───────────────────────────────────────────────────────────────

/// Top-level application state for the inspect TUI.
pub(crate) struct InspectApp {
    pub(crate) should_quit: bool,
    pub(crate) terminal_width: u16,
    pub(crate) input_mode: InputMode,
    pub(crate) show_help: bool,
}

impl InspectApp {
    /// Create a new `InspectApp` with default state.
    pub(crate) fn new() -> Self {
        Self {
            should_quit: false,
            terminal_width: 0,
            input_mode: InputMode::Normal,
            show_help: false,
        }
    }

    /// Main event loop: poll for events, dispatch input, and draw.
    pub(crate) fn run(
        &mut self,
        terminal: &mut Terminal<CrosstermBackend<std::io::Stdout>>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        loop {
            // Update terminal width from the current frame size.
            self.terminal_width = terminal.size()?.width;

            terminal.draw(|frame| ui::draw(frame, self))?;

            if self.should_quit {
                break;
            }

            if event::poll(std::time::Duration::from_millis(50))? {
                match event::read()? {
                    Event::Key(key) => input::handle_key(self, key),
                    Event::Mouse(mouse) => input::handle_mouse(self, mouse),
                    _ => {}
                }
            }
        }
        Ok(())
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_app_starts_in_normal_mode() {
        let app = InspectApp::new();
        assert_eq!(
            app.input_mode,
            InputMode::Normal,
            "app should start in normal input mode"
        );
        assert!(
            !app.should_quit,
            "app should not be in quit state on creation"
        );
        assert!(!app.show_help, "help overlay should be hidden on creation");
    }
}
