//! Key and mouse event handling for `oxitest inspect`.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use super::app::{InputMode, InspectApp};

/// Process a key event and update application state.
pub(crate) fn handle_key(app: &mut InspectApp, key: KeyEvent) {
    match &app.input_mode {
        InputMode::Normal => handle_normal_key(app, key),
        InputMode::Search { .. } => handle_search_key(app, key),
    }
}

/// Process a mouse event and update application state.
pub(crate) fn handle_mouse(app: &mut InspectApp, mouse: MouseEvent) {
    match mouse.kind {
        MouseEventKind::ScrollUp | MouseEventKind::ScrollDown => {
            // Scroll events — no-op until tree navigation is implemented.
        }
        MouseEventKind::Down(MouseButton::Left) => {
            // Click to select — no-op until tree navigation is implemented.
        }
        _ => {}
    }
    // Suppress unused variable warning until navigation is wired up.
    let _ = app;
}

// ── Normal mode ──────────────────────────────────────────────────────────────

fn handle_normal_key(app: &mut InspectApp, key: KeyEvent) {
    // Ctrl+C always quits, regardless of mode.
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        app.should_quit = true;
        return;
    }

    match key.code {
        // Quit
        KeyCode::Char('q') | KeyCode::Esc => app.should_quit = true,

        // Vertical movement (no-op until tree data is loaded)
        KeyCode::Char('j') | KeyCode::Down => {}
        KeyCode::Char('k') | KeyCode::Up => {}

        // Horizontal navigation (no-op until tree data is loaded)
        KeyCode::Char('h') | KeyCode::Left | KeyCode::Backspace => {}
        KeyCode::Char('l') | KeyCode::Right | KeyCode::Char(' ') => {}

        // Enter search mode
        KeyCode::Char('/') => {
            app.input_mode = InputMode::Search {
                query: String::new(),
            };
        }

        // Toggle help overlay
        KeyCode::Char('?') => {
            app.show_help = !app.show_help;
        }

        // Toggle source view (no-op until #1117)
        KeyCode::Char('s') => {}

        _ => {}
    }
}

// ── Search mode ──────────────────────────────────────────────────────────────

fn handle_search_key(app: &mut InspectApp, key: KeyEvent) {
    // Ctrl+C always quits, regardless of mode.
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        app.should_quit = true;
        return;
    }

    match key.code {
        // Exit search mode, return to normal
        KeyCode::Esc => {
            app.input_mode = InputMode::Normal;
        }
        // Accept search (no-op until search is wired up)
        KeyCode::Enter => {
            app.input_mode = InputMode::Normal;
        }
        // Delete last character from search query
        KeyCode::Backspace => {
            if let InputMode::Search { query } = &mut app.input_mode {
                query.pop();
            }
        }
        // Append character to search query
        KeyCode::Char(c) => {
            if let InputMode::Search { query } = &mut app.input_mode {
                query.push(c);
            }
        }
        _ => {}
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyEventKind, KeyEventState};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent {
            code,
            modifiers: KeyModifiers::NONE,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        }
    }

    #[test]
    fn input_key_q_sets_quit() {
        let mut app = InspectApp::new();
        handle_key(&mut app, key(KeyCode::Char('q')));
        assert!(
            app.should_quit,
            "pressing 'q' in normal mode should set should_quit"
        );
    }

    #[test]
    fn input_key_esc_sets_quit_in_normal() {
        let mut app = InspectApp::new();
        handle_key(&mut app, key(KeyCode::Esc));
        assert!(
            app.should_quit,
            "pressing Esc in normal mode should set should_quit"
        );
    }

    #[test]
    fn input_key_slash_enters_search() {
        let mut app = InspectApp::new();
        handle_key(&mut app, key(KeyCode::Char('/')));
        assert_eq!(
            app.input_mode,
            InputMode::Search {
                query: String::new()
            },
            "pressing '/' should transition to search mode with empty query"
        );
    }

    #[test]
    fn input_key_esc_exits_search() {
        let mut app = InspectApp::new();
        app.input_mode = InputMode::Search {
            query: "foo".to_string(),
        };
        handle_key(&mut app, key(KeyCode::Esc));
        assert_eq!(
            app.input_mode,
            InputMode::Normal,
            "pressing Esc in search mode should return to normal mode"
        );
        assert!(
            !app.should_quit,
            "Esc in search mode should not quit the app"
        );
    }

    #[test]
    fn input_search_mode_appends_chars() {
        let mut app = InspectApp::new();
        app.input_mode = InputMode::Search {
            query: String::new(),
        };
        handle_key(&mut app, key(KeyCode::Char('a')));
        handle_key(&mut app, key(KeyCode::Char('b')));
        assert_eq!(
            app.input_mode,
            InputMode::Search {
                query: "ab".to_string()
            },
            "typing in search mode should append characters to the query"
        );
    }

    #[test]
    fn input_search_mode_backspace_removes_char() {
        let mut app = InspectApp::new();
        app.input_mode = InputMode::Search {
            query: "abc".to_string(),
        };
        handle_key(&mut app, key(KeyCode::Backspace));
        assert_eq!(
            app.input_mode,
            InputMode::Search {
                query: "ab".to_string()
            },
            "backspace in search mode should remove the last character"
        );
    }

    #[test]
    fn input_question_mark_toggles_help() {
        let mut app = InspectApp::new();
        assert!(!app.show_help, "help should start hidden");
        handle_key(&mut app, key(KeyCode::Char('?')));
        assert!(app.show_help, "pressing '?' should show help overlay");
        handle_key(&mut app, key(KeyCode::Char('?')));
        assert!(
            !app.show_help,
            "pressing '?' again should hide help overlay"
        );
    }

    #[test]
    fn input_ctrl_c_quits_from_any_mode() {
        let mut app = InspectApp::new();
        app.input_mode = InputMode::Search {
            query: "test".to_string(),
        };
        let ctrl_c = KeyEvent {
            code: KeyCode::Char('c'),
            modifiers: KeyModifiers::CONTROL,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        };
        handle_key(&mut app, ctrl_c);
        assert!(app.should_quit, "Ctrl+C should quit from search mode");
    }
}
