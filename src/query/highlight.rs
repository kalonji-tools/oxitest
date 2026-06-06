//! Lexer-driven Python source highlighting for query inspect previews.
//!
//! Uses `rustpython_parser::lexer::lex` to tokenize Python source and maps
//! each token type to a color function. Gaps between tokens (whitespace,
//! newlines) are emitted as-is.

use crate::colors::{color_blue, color_bold_cyan, color_dim_cyan, color_green};
use rustpython_parser::lexer;
use rustpython_parser::Mode;
use rustpython_parser::Tok;

/// Highlight Python source code with ANSI colors.
///
/// Returns the source unchanged when `use_color` is false.
pub(crate) fn highlight_python(source: &str, use_color: bool) -> String {
    if !use_color {
        return source.to_string();
    }

    let mut out = String::with_capacity(source.len() * 2);
    let mut prev_end: usize = 0;
    let mut after_at = false;

    for result in lexer::lex(source, Mode::Module) {
        let Ok((tok, range)) = result else {
            // On lex error, emit remaining source uncolored and bail.
            out.push_str(&source[prev_end..]);
            return out;
        };

        let start = range.start().to_usize();
        let end = range.end().to_usize();

        // Emit gap (whitespace/newlines between tokens).
        if start > prev_end {
            out.push_str(&source[prev_end..start]);
        }

        let token_text = &source[start..end];

        let colored = match &tok {
            // Keywords
            Tok::Def
            | Tok::Class
            | Tok::If
            | Tok::Elif
            | Tok::Else
            | Tok::Return
            | Tok::Assert
            | Tok::Async
            | Tok::Await
            | Tok::For
            | Tok::While
            | Tok::With
            | Tok::Try
            | Tok::Except
            | Tok::Finally
            | Tok::Raise
            | Tok::Yield
            | Tok::Import
            | Tok::From
            | Tok::As
            | Tok::In
            | Tok::Not
            | Tok::And
            | Tok::Or
            | Tok::Is
            | Tok::Pass
            | Tok::Break
            | Tok::Continue
            | Tok::Del
            | Tok::Global
            | Tok::Nonlocal
            | Tok::Lambda
            | Tok::Match
            | Tok::Case
            | Tok::Type => color_bold_cyan(token_text, true),

            // Built-in constants
            Tok::None | Tok::True | Tok::False => color_bold_cyan(token_text, true),

            // Strings
            Tok::String { .. } => color_green(token_text, true),

            // Numeric literals
            Tok::Int { .. } | Tok::Float { .. } | Tok::Complex { .. } => {
                color_blue(token_text, true)
            }

            // Decorator @
            Tok::At => {
                after_at = true;
                color_dim_cyan(token_text, true)
            }

            // Name following @ (decorator name)
            Tok::Name { .. } if after_at => {
                after_at = false;
                color_dim_cyan(token_text, true)
            }

            // Everything else: default
            _ => {
                after_at = false;
                token_text.to_string()
            }
        };

        // Reset after_at for non-name tokens (except At itself, handled above)
        if !matches!(&tok, Tok::At | Tok::Name { .. }) {
            after_at = false;
        }

        out.push_str(&colored);
        prev_end = end;
    }

    // Emit any remaining source after the last token.
    if prev_end < source.len() {
        out.push_str(&source[prev_end..]);
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_color_returns_unchanged() {
        let source = "def test_foo():\n    assert True\n";
        assert_eq!(highlight_python(source, false), source);
    }

    #[test]
    fn keywords_are_colored() {
        console::set_colors_enabled(true);
        let out = highlight_python("def foo():\n    return None\n", true);
        console::set_colors_enabled(false);
        assert!(out.contains("\x1b["), "expected ANSI escapes in output");
        assert!(out.contains("def"));
        assert!(out.contains("return"));
        assert!(out.contains("None"));
    }

    #[test]
    fn strings_are_colored() {
        console::set_colors_enabled(true);
        let out = highlight_python("x = \"hello\"\n", true);
        console::set_colors_enabled(false);
        assert!(out.contains("hello"));
        assert!(out.contains("\x1b["), "expected ANSI escapes for string");
    }

    #[test]
    fn comments_are_preserved() {
        // Comments are not tokenized without the `full-lexer` feature, so they
        // fall through the gap-emission path and appear verbatim.
        let out = highlight_python("# this is a comment\nx = 1\n", true);
        assert!(out.contains("# this is a comment"));
    }

    #[test]
    fn decorator_is_colored() {
        console::set_colors_enabled(true);
        let out = highlight_python("@mark.parametrize\ndef test(): pass\n", true);
        console::set_colors_enabled(false);
        assert!(out.contains("@"));
        assert!(out.contains("mark"));
    }

    #[test]
    fn numeric_literals_are_colored() {
        console::set_colors_enabled(true);
        let out = highlight_python("x = 42\ny = 3.14\n", true);
        console::set_colors_enabled(false);
        assert!(out.contains("42"));
        assert!(out.contains("3.14"));
    }

    #[test]
    fn snap_highlight_test_function() {
        let source = "\
@mark.parametrize(\"x\", [1, 2, 3])
def test_add(x):
    # basic addition
    result = x + 1
    assert result > 0
";
        console::set_colors_enabled(true);
        let result = highlight_python(source, true);
        console::set_colors_enabled(false);
        insta::assert_snapshot!(result);
    }

    #[test]
    fn snap_highlight_no_color() {
        let source = "def test_foo():\n    assert True\n";
        insta::assert_snapshot!(highlight_python(source, false));
    }
}
