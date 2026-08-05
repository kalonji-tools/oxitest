//! DSL lexer and parser for the unified query system.
//!
//! Parses expressions like `name(~foo) & mark(=slow) | !async()` into an AST.

use super::ast::{DslError, Expr, Matcher, Token};

/// Return `true` if `c` is a valid bare-word character in the DSL.
fn is_bare_word_char(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '_' | '.' | ':' | '-')
}

// ── Lexer ─────────────────────────────────────────────────────────────────────

/// Tokenize a DSL expression string.
///
/// Context-sensitive: bare words after `(`, `~`, or `=` become [`Token::Str`];
/// otherwise bare words become [`Token::Ident`] (or a keyword token).
///
/// # Errors
///
/// Returns [`DslError::UnterminatedString`] or [`DslError::UnterminatedRegex`]
/// if a literal is not closed before end-of-input.
pub fn lex(input: &str) -> Result<Vec<Token>, DslError> {
    let chars: Vec<char> = input.chars().collect();
    let len = chars.len();
    let mut pos = 0;
    let mut tokens: Vec<Token> = Vec::new();

    // Pre-compute byte offset for each char index (for miette spans on errors).
    let byte_offsets: Vec<usize> = chars
        .iter()
        .scan(0usize, |acc, c| {
            let off = *acc;
            *acc += c.len_utf8();
            Some(off)
        })
        .collect();
    let total_bytes = input.len();
    let byte_at = |pos: usize| -> usize {
        if pos < byte_offsets.len() {
            byte_offsets[pos]
        } else {
            total_bytes
        }
    };

    // Track whether the previous non-whitespace token was an Ident (predicate
    // name). When `(` follows an Ident it is a predicate argument list, so the
    // first bare word inside should be a Str. When `(` follows anything else it
    // is a grouping paren and the first token inside is still an Ident.
    // Additionally, after `~` or `=` the next bare word is always a Str.
    let mut prev_was_ident = false;
    let mut next_bare_is_str = false;

    while pos < len {
        match chars[pos] {
            // Skip whitespace
            c if c.is_ascii_whitespace() => {
                pos += 1;
            }

            // Single-character tokens
            '(' => {
                tokens.push(Token::LParen);
                // Only treat the next bare word as Str when opening a predicate
                // argument list (i.e., directly after an identifier).
                next_bare_is_str = prev_was_ident;
                prev_was_ident = false;
                pos += 1;
            }
            ')' => {
                tokens.push(Token::RParen);
                next_bare_is_str = false;
                prev_was_ident = false;
                pos += 1;
            }
            '&' => {
                tokens.push(Token::Amp);
                next_bare_is_str = false;
                prev_was_ident = false;
                pos += 1;
            }
            '|' => {
                tokens.push(Token::Pipe);
                next_bare_is_str = false;
                prev_was_ident = false;
                pos += 1;
            }
            '!' => {
                tokens.push(Token::Bang);
                next_bare_is_str = false;
                prev_was_ident = false;
                pos += 1;
            }
            '~' => {
                tokens.push(Token::Tilde);
                next_bare_is_str = true;
                prev_was_ident = false;
                pos += 1;
            }
            '=' => {
                tokens.push(Token::Eq);
                next_bare_is_str = true;
                prev_was_ident = false;
                pos += 1;
            }

            // Regex literal /pattern/
            '/' => {
                pos += 1; // skip opening '/'
                let start = pos;
                while pos < len && chars[pos] != '/' {
                    pos += 1;
                }
                if pos >= len {
                    return Err(DslError::UnterminatedRegex {
                        span: (byte_at(start - 1), total_bytes - byte_at(start - 1)).into(),
                    });
                }
                let pattern: String = chars[start..pos].iter().collect();
                pos += 1; // skip closing '/'
                tokens.push(Token::Regex(pattern));
                next_bare_is_str = false;
                prev_was_ident = false;
            }

            // Double-quoted string
            '"' => {
                pos += 1; // skip opening '"'
                let start = pos;
                while pos < len && chars[pos] != '"' {
                    pos += 1;
                }
                if pos >= len {
                    return Err(DslError::UnterminatedString {
                        span: (byte_at(start - 1), total_bytes - byte_at(start - 1)).into(),
                    });
                }
                let s: String = chars[start..pos].iter().collect();
                pos += 1; // skip closing '"'
                tokens.push(Token::Str(s));
                next_bare_is_str = false;
                prev_was_ident = false;
            }

            // Single-quoted string
            '\'' => {
                pos += 1; // skip opening '\''
                let start = pos;
                while pos < len && chars[pos] != '\'' {
                    pos += 1;
                }
                if pos >= len {
                    return Err(DslError::UnterminatedString {
                        span: (byte_at(start - 1), total_bytes - byte_at(start - 1)).into(),
                    });
                }
                let s: String = chars[start..pos].iter().collect();
                pos += 1; // skip closing '\''
                tokens.push(Token::Str(s));
                next_bare_is_str = false;
                prev_was_ident = false;
            }

            // Bare word: alphanumeric, _, ., :, -
            c if is_bare_word_char(c) => {
                let start = pos;
                while pos < len {
                    let ch = chars[pos];
                    if is_bare_word_char(ch) {
                        pos += 1;
                    } else {
                        break;
                    }
                }
                let word: String = chars[start..pos].iter().collect();

                if next_bare_is_str {
                    // Inside predicate argument list or after ~ / = — treat as Str
                    tokens.push(Token::Str(word));
                    next_bare_is_str = false;
                    prev_was_ident = false;
                } else {
                    // Could be a keyword or an ident
                    let tok = match word.as_str() {
                        "and" => {
                            prev_was_ident = false;
                            Token::And
                        }
                        "or" => {
                            prev_was_ident = false;
                            Token::Or
                        }
                        "not" => {
                            prev_was_ident = false;
                            Token::Not
                        }
                        _ => {
                            prev_was_ident = true;
                            Token::Ident(word)
                        }
                    };
                    next_bare_is_str = false;
                    tokens.push(tok);
                }
            }

            other => {
                return Err(DslError::ParseError {
                    message: format!("unexpected character '{other}'"),
                });
            }
        }
    }

    Ok(tokens)
}

// ── Parser ────────────────────────────────────────────────────────────────────

struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

impl Parser {
    const fn new(tokens: Vec<Token>) -> Self {
        Self { tokens, pos: 0 }
    }

    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.pos)
    }

    fn advance(&mut self) -> Option<&Token> {
        let tok = self.tokens.get(self.pos);
        self.pos += 1;
        tok
    }

    fn expect(&mut self, expected: &Token) -> Result<(), DslError> {
        match self.peek() {
            Some(t) if t == expected => {
                self.advance();
                Ok(())
            }
            Some(t) => Err(DslError::ParseError {
                message: format!("expected {expected:?}, got {t:?}"),
            }),
            None => Err(DslError::ParseError {
                message: format!("expected {expected:?}, got end of input"),
            }),
        }
    }

    /// expr = term (("&" | "and") term | ("|" | "or") term)*
    fn parse_expr(&mut self) -> Result<Expr, DslError> {
        let mut left = self.parse_term()?;
        loop {
            match self.peek() {
                Some(Token::Amp) | Some(Token::And) => {
                    self.advance();
                    let right = self.parse_term()?;
                    left = Expr::And(Box::new(left), Box::new(right));
                }
                Some(Token::Pipe) | Some(Token::Or) => {
                    self.advance();
                    let right = self.parse_term()?;
                    left = Expr::Or(Box::new(left), Box::new(right));
                }
                _ => break,
            }
        }
        Ok(left)
    }

    /// term = ("not" | "!") term | atom
    fn parse_term(&mut self) -> Result<Expr, DslError> {
        match self.peek() {
            Some(Token::Bang) | Some(Token::Not) => {
                self.advance();
                let inner = self.parse_term()?;
                Ok(Expr::Not(Box::new(inner)))
            }
            _ => self.parse_atom(),
        }
    }

    /// atom = predicate | "(" expr ")"
    fn parse_atom(&mut self) -> Result<Expr, DslError> {
        match self.peek().cloned() {
            Some(Token::LParen) => {
                self.advance(); // consume '('
                let inner = self.parse_expr()?;
                self.expect(&Token::RParen)
                    .map_err(|_| DslError::UnmatchedParen)?;
                Ok(inner)
            }
            Some(Token::Ident(name)) => {
                self.advance(); // consume ident
                self.parse_predicate(name)
            }
            Some(Token::RParen) => Err(DslError::UnmatchedParen),
            None => Err(DslError::EmptyExpression),
            Some(other) => Err(DslError::ParseError {
                message: format!("unexpected token {other:?}"),
            }),
        }
    }

    /// `predicate = IDENT "(" [matcher] ")"`
    fn parse_predicate(&mut self, name: String) -> Result<Expr, DslError> {
        match self.peek() {
            Some(Token::LParen) => {}
            _ => return Err(DslError::MissingParens { name }),
        }
        self.advance(); // consume '('

        let matcher = match self.peek().cloned() {
            // Empty parens → Any matcher
            Some(Token::RParen) => Matcher::Any,

            // ~value → Contains
            Some(Token::Tilde) => {
                self.advance(); // consume '~'
                match self.advance().cloned() {
                    Some(Token::Str(s)) => Matcher::Contains(s),
                    other => {
                        return Err(DslError::ParseError {
                            message: format!("expected string after '~', got {other:?}"),
                        });
                    }
                }
            }

            // =value → Exact
            Some(Token::Eq) => {
                self.advance(); // consume '='
                match self.advance().cloned() {
                    Some(Token::Str(s)) => Matcher::Exact(s),
                    other => {
                        return Err(DslError::ParseError {
                            message: format!("expected string after '=', got {other:?}"),
                        });
                    }
                }
            }

            // /pattern/ → Regex (compiled eagerly at parse time)
            Some(Token::Regex(pattern)) => {
                self.advance();
                let re = regex::Regex::new(&pattern).map_err(|e| DslError::InvalidRegex {
                    pattern: pattern.clone(),
                    reason: e.to_string(),
                })?;
                Matcher::Regex(re)
            }

            // bare Str → Contains (default)
            Some(Token::Str(s)) => {
                self.advance(); // consume string
                Matcher::Contains(s)
            }

            other => {
                return Err(DslError::ParseError {
                    message: format!("unexpected token in predicate argument: {other:?}"),
                });
            }
        };

        self.expect(&Token::RParen)?;
        Ok(Expr::Predicate { name, matcher })
    }
}

/// Parse a token stream into an [`Expr`] AST.
///
/// # Errors
///
/// Returns a [`DslError`] if the token stream does not form a valid expression.
pub fn parse(tokens: Vec<Token>) -> Result<Expr, DslError> {
    if tokens.is_empty() {
        return Err(DslError::EmptyExpression);
    }
    let mut parser = Parser::new(tokens);
    let expr = parser.parse_expr()?;
    if parser.pos < parser.tokens.len() {
        return Err(DslError::ParseError {
            message: format!("unexpected token {:?}", parser.tokens[parser.pos]),
        });
    }
    Ok(expr)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── Lexer tests ───────────────────────────────────────────────────────────

    #[test]
    fn lex_simple_predicate() {
        let tokens = lex("name(foo)").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Ident("name".into()),
                Token::LParen,
                Token::Str("foo".into()),
                Token::RParen,
            ]
        );
    }

    #[test]
    fn lex_contains_matcher() {
        let tokens = lex("name(~bar)").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Ident("name".into()),
                Token::LParen,
                Token::Tilde,
                Token::Str("bar".into()),
                Token::RParen,
            ]
        );
    }

    #[test]
    fn lex_exact_matcher() {
        let tokens = lex("name(=exact)").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Ident("name".into()),
                Token::LParen,
                Token::Eq,
                Token::Str("exact".into()),
                Token::RParen,
            ]
        );
    }

    #[test]
    fn lex_regex_matcher() {
        let tokens = lex("name(/test_.*/)").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Ident("name".into()),
                Token::LParen,
                Token::Regex("test_.*".into()),
                Token::RParen,
            ]
        );
    }

    #[test]
    fn lex_boolean_operators() {
        let tokens = lex("a(x) & b(y) | !c(z)").unwrap();
        // Check that operator tokens appear at the right positions
        assert_eq!(tokens[4], Token::Amp);
        assert_eq!(tokens[9], Token::Pipe);
        assert_eq!(tokens[10], Token::Bang);
    }

    #[test]
    fn lex_word_operators() {
        let tokens = lex("a(x) and b(y) or not c(z)").unwrap();
        assert_eq!(tokens[4], Token::And);
        assert_eq!(tokens[9], Token::Or);
        assert_eq!(tokens[10], Token::Not);
    }

    #[test]
    fn lex_parens_grouping() {
        let tokens = lex("(a(x) | b(y)) & c(z)").unwrap();
        assert_eq!(tokens[0], Token::LParen);
    }

    #[test]
    fn lex_quoted_string() {
        let tokens = lex(r#"name("hello world")"#).unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Ident("name".into()),
                Token::LParen,
                Token::Str("hello world".into()),
                Token::RParen,
            ]
        );
    }

    #[test]
    fn lex_single_quoted_string() {
        let tokens = lex("name('hello world')").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Ident("name".into()),
                Token::LParen,
                Token::Str("hello world".into()),
                Token::RParen,
            ]
        );
    }

    #[test]
    fn lex_empty_parens() {
        let tokens = lex("async()").unwrap();
        assert_eq!(
            tokens,
            vec![Token::Ident("async".into()), Token::LParen, Token::RParen,]
        );
    }

    #[test]
    fn lex_error_unterminated_string() {
        assert!(matches!(
            lex(r#"name("foo)"#),
            Err(DslError::UnterminatedString { .. })
        ));
    }

    #[test]
    fn lex_error_unterminated_string_span() {
        // name("foo  — quote starts at byte 5, spans to end (byte 10)
        let err = lex(r#"name("foo)"#).unwrap_err();
        match err {
            DslError::UnterminatedString { span } => {
                assert_eq!(span.offset(), 5, "span should start at the opening quote");
                assert_eq!(span.len(), 5, "span should cover from quote to end");
            }
            other => panic!("expected UnterminatedString, got {other:?}"),
        }
    }

    #[test]
    fn lex_error_unterminated_regex() {
        assert!(matches!(
            lex("name(/foo)"),
            Err(DslError::UnterminatedRegex { .. })
        ));
    }

    #[test]
    fn lex_error_unterminated_regex_span() {
        // name(/foo  — slash starts at byte 5, spans to end (byte 9)
        let err = lex("name(/foo").unwrap_err();
        match err {
            DslError::UnterminatedRegex { span } => {
                assert_eq!(span.offset(), 5, "span should start at the opening /");
                assert_eq!(span.len(), 4, "span should cover from / to end");
            }
            other => panic!("expected UnterminatedRegex, got {other:?}"),
        }
    }

    #[test]
    fn lex_error_unexpected_char() {
        let err = lex("name(foo) #").unwrap_err();
        assert!(
            matches!(err, DslError::ParseError { .. }),
            "expected ParseError, got {err:?}"
        );
    }

    // ── Parser tests ──────────────────────────────────────────────────────────

    fn lex_and_parse(input: &str) -> Result<Expr, DslError> {
        let tokens = lex(input)?;
        parse(tokens)
    }

    #[test]
    fn parse_simple_predicate() {
        let expr = lex_and_parse("name(foo)").unwrap();
        assert!(
            matches!(&expr, Expr::Predicate { name, matcher: Matcher::Contains(s) } if name == "name" && s == "foo"),
            "expected Predicate with Contains(\"foo\"), got {expr:?}"
        );
    }

    #[test]
    fn parse_exact_matcher() {
        let expr = lex_and_parse("name(=exact)").unwrap();
        assert!(
            matches!(&expr, Expr::Predicate { name, matcher: Matcher::Exact(s) } if name == "name" && s == "exact"),
            "expected Predicate with Exact(\"exact\"), got {expr:?}"
        );
    }

    #[test]
    fn parse_contains_matcher() {
        let expr = lex_and_parse("name(~partial)").unwrap();
        assert!(
            matches!(&expr, Expr::Predicate { name, matcher: Matcher::Contains(s) } if name == "name" && s == "partial"),
            "expected Predicate with Contains(\"partial\"), got {expr:?}"
        );
    }

    #[test]
    fn parse_regex_matcher() {
        let expr = lex_and_parse("name(/test_.*/)").unwrap();
        match &expr {
            Expr::Predicate {
                name,
                matcher: Matcher::Regex(re),
            } => {
                assert_eq!(name, "name", "predicate name mismatch");
                assert_eq!(re.as_str(), "test_.*", "regex pattern mismatch");
            }
            other => panic!("expected Predicate with Regex, got {other:?}"),
        }
    }

    #[test]
    fn parse_boolean_predicate() {
        let expr = lex_and_parse("async()").unwrap();
        assert!(
            matches!(&expr, Expr::Predicate { name, matcher: Matcher::Any } if name == "async"),
            "expected Predicate with Any for async(), got {expr:?}"
        );
    }

    #[test]
    fn parse_and() {
        let expr = lex_and_parse("a(x) & b(y)").unwrap();
        assert!(matches!(expr, Expr::And(_, _)));
    }

    #[test]
    fn parse_or() {
        let expr = lex_and_parse("a(x) | b(y)").unwrap();
        assert!(matches!(expr, Expr::Or(_, _)));
    }

    #[test]
    fn parse_not() {
        let expr = lex_and_parse("!a(x)").unwrap();
        assert!(matches!(expr, Expr::Not(_)));
    }

    #[test]
    fn parse_precedence_not_over_and() {
        // !a(x) & b(y) should parse as And(Not(a), b), not Not(And(a, b))
        let expr = lex_and_parse("!a(x) & b(y)").unwrap();
        assert!(
            matches!(expr, Expr::And(ref l, _) if matches!(l.as_ref(), Expr::Not(_))),
            "expected And(Not(...), ...) but got {expr:?}"
        );
    }

    #[test]
    fn parse_parens_override_precedence() {
        // !(a(x) & b(y)) should parse as Not(And(a, b))
        let expr = lex_and_parse("!(a(x) & b(y))").unwrap();
        assert!(
            matches!(expr, Expr::Not(ref inner) if matches!(inner.as_ref(), Expr::And(_, _))),
            "expected Not(And(...)) but got {expr:?}"
        );
    }

    #[test]
    fn parse_error_empty() {
        assert!(matches!(lex_and_parse(""), Err(DslError::EmptyExpression)));
    }

    #[test]
    fn parse_error_unmatched_paren() {
        assert!(matches!(
            lex_and_parse("(a(x)"),
            Err(DslError::UnmatchedParen | DslError::ParseError { .. })
        ));
    }

    #[test]
    fn parse_error_missing_parens_on_predicate() {
        // "name" without parens — token stream is just [Ident("name")]
        // parse_predicate sees no LParen → MissingParens
        let result = lex_and_parse("name");
        assert!(
            matches!(result, Err(DslError::MissingParens { .. })),
            "expected MissingParens, got {result:?}"
        );
    }

    // ── is_bare_word_char tests ───────────────────────────────────────────────

    #[test]
    fn bare_word_char_accepts_ascii_letters() {
        assert!(is_bare_word_char('a'));
        assert!(is_bare_word_char('z'));
        assert!(is_bare_word_char('A'));
        assert!(is_bare_word_char('Z'));
    }

    #[test]
    fn bare_word_char_accepts_digits() {
        assert!(is_bare_word_char('0'));
        assert!(is_bare_word_char('9'));
    }

    #[test]
    fn bare_word_char_accepts_special_chars() {
        assert!(is_bare_word_char('_'));
        assert!(is_bare_word_char('.'));
        assert!(is_bare_word_char(':'));
        assert!(is_bare_word_char('-'));
    }

    #[test]
    fn bare_word_char_rejects_operators() {
        assert!(!is_bare_word_char('('));
        assert!(!is_bare_word_char(')'));
        assert!(!is_bare_word_char('&'));
        assert!(!is_bare_word_char('!'));
    }

    #[test]
    fn bare_word_char_rejects_whitespace() {
        assert!(!is_bare_word_char(' '));
        assert!(!is_bare_word_char('\t'));
    }

    // ── miette diagnostic rendering ──────────────────────────────────────

    #[test]
    fn miette_renders_unterminated_string_with_span() {
        use miette::{GraphicalReportHandler, GraphicalTheme};

        let input = r#"name("foo"#;
        let err = lex(input).unwrap_err();
        let report = miette::Report::new(err).with_source_code(input.to_string());
        let mut buf = String::new();
        let handler = GraphicalReportHandler::new_themed(GraphicalTheme::unicode_nocolor());
        handler.render_report(&mut buf, report.as_ref()).unwrap();
        assert!(
            buf.contains("string starts here"),
            "diagnostic should contain the label, got:\n{buf}"
        );
        assert!(
            buf.contains("close the string"),
            "diagnostic should contain the help text, got:\n{buf}"
        );
    }

    #[test]
    fn miette_renders_unterminated_regex_with_span() {
        use miette::{GraphicalReportHandler, GraphicalTheme};

        let input = "name(/foo";
        let err = lex(input).unwrap_err();
        let report = miette::Report::new(err).with_source_code(input.to_string());
        let mut buf = String::new();
        let handler = GraphicalReportHandler::new_themed(GraphicalTheme::unicode_nocolor());
        handler.render_report(&mut buf, report.as_ref()).unwrap();
        assert!(
            buf.contains("regex starts here"),
            "diagnostic should contain the label, got:\n{buf}"
        );
        assert!(
            buf.contains("close the regex"),
            "diagnostic should contain the help text, got:\n{buf}"
        );
    }
}
