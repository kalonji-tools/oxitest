//! DSL lexer, parser, and evaluator for the unified query system.
//!
//! Parses expressions like `name(~foo) & mark(=slow) | !async()` into an AST
//! and evaluates them against [`crate::query::resource::QueryEntry`] field maps.

use regex::Regex;

// ── Token ──────────────────────────────────────────────────────────────────────

/// Tokens produced by [`lex`].
#[derive(Debug, PartialEq, Clone)]
pub(crate) enum Token {
    /// A bare identifier (predicate name or word-form operator).
    Ident(String),
    /// A string value (quoted or bare word inside parentheses).
    Str(String),
    /// A regex literal `/pattern/`.
    Regex(String),
    /// `(`
    LParen,
    /// `)`
    RParen,
    /// `&`
    Amp,
    /// `|`
    Pipe,
    /// `!`
    Bang,
    /// `~`
    Tilde,
    /// `=`
    Eq,
    /// `and` keyword
    And,
    /// `or` keyword
    Or,
    /// `not` keyword
    Not,
}

// ── Error ─────────────────────────────────────────────────────────────────────

/// Errors produced by the DSL lexer, parser, and validator.
#[derive(thiserror::Error, Debug, PartialEq)]
pub(crate) enum DslError {
    /// A string literal was not properly terminated.
    #[error("unterminated string literal")]
    UnterminatedString,
    /// A regex literal was not properly terminated.
    #[error("unterminated regex literal")]
    UnterminatedRegex,
    /// The expression string is empty.
    #[error("empty expression")]
    EmptyExpression,
    /// An unmatched closing parenthesis was encountered.
    #[error("unmatched ')'")]
    UnmatchedParen,
    /// A predicate was missing its `(...)` argument list.
    #[error("expected '(' after predicate name '{0}'")]
    MissingParens(String),
    /// A general parse error with a message.
    #[error("parse error: {0}")]
    ParseError(String),
    /// A predicate name is not valid for the given resource kind.
    #[error("predicate '{predicate}' is not valid for resource '{resource}'")]
    InvalidPredicate {
        /// The predicate name used.
        predicate: String,
        /// The resource kind name.
        resource: String,
    },
    /// A regex pattern failed to compile.
    #[error("invalid regex pattern '{0}': {1}")]
    InvalidRegex(String, String),
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
pub(crate) fn lex(input: &str) -> Result<Vec<Token>, DslError> {
    let chars: Vec<char> = input.chars().collect();
    let len = chars.len();
    let mut pos = 0;
    let mut tokens: Vec<Token> = Vec::new();

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
                    return Err(DslError::UnterminatedRegex);
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
                    return Err(DslError::UnterminatedString);
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
                    return Err(DslError::UnterminatedString);
                }
                let s: String = chars[start..pos].iter().collect();
                pos += 1; // skip closing '\''
                tokens.push(Token::Str(s));
                next_bare_is_str = false;
                prev_was_ident = false;
            }

            // Bare word: alphanumeric, _, ., :, -
            c if c.is_alphanumeric() || matches!(c, '_' | '.' | ':' | '-') => {
                let start = pos;
                while pos < len {
                    let ch = chars[pos];
                    if ch.is_alphanumeric() || matches!(ch, '_' | '.' | ':' | '-') {
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
                return Err(DslError::ParseError(format!(
                    "unexpected character '{other}'"
                )))
            }
        }
    }

    Ok(tokens)
}

// ── AST ───────────────────────────────────────────────────────────────────────

/// How a predicate argument matches a field value.
#[derive(Debug, PartialEq, Clone)]
pub(crate) enum Matcher {
    /// Field exists and is non-empty and non-"false" (boolean predicate).
    Any,
    /// Any comma-separated value contains the string (substring match).
    Contains(String),
    /// Any comma-separated value exactly equals the string.
    Exact(String),
    /// Any comma-separated value matches the regex pattern.
    Regex(String),
}

/// A parsed DSL expression tree.
#[derive(Debug, PartialEq, Clone)]
pub(crate) enum Expr {
    /// A single predicate: `field_name(matcher)`.
    Predicate {
        /// The field name to look up.
        name: String,
        /// The matcher to apply.
        matcher: Matcher,
    },
    /// Logical AND of two sub-expressions.
    And(Box<Expr>, Box<Expr>),
    /// Logical OR of two sub-expressions.
    Or(Box<Expr>, Box<Expr>),
    /// Logical NOT of a sub-expression.
    Not(Box<Expr>),
}

// ── Parser ────────────────────────────────────────────────────────────────────

struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

impl Parser {
    fn new(tokens: Vec<Token>) -> Self {
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
            Some(t) => Err(DslError::ParseError(format!(
                "expected {expected:?}, got {t:?}"
            ))),
            None => Err(DslError::ParseError(format!(
                "expected {expected:?}, got end of input"
            ))),
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
            Some(other) => Err(DslError::ParseError(format!("unexpected token {other:?}"))),
        }
    }

    /// predicate = IDENT "(" [matcher] ")"
    fn parse_predicate(&mut self, name: String) -> Result<Expr, DslError> {
        match self.peek() {
            Some(Token::LParen) => {}
            _ => return Err(DslError::MissingParens(name)),
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
                        return Err(DslError::ParseError(format!(
                            "expected string after '~', got {other:?}"
                        )))
                    }
                }
            }

            // =value → Exact
            Some(Token::Eq) => {
                self.advance(); // consume '='
                match self.advance().cloned() {
                    Some(Token::Str(s)) => Matcher::Exact(s),
                    other => {
                        return Err(DslError::ParseError(format!(
                            "expected string after '=', got {other:?}"
                        )))
                    }
                }
            }

            // /pattern/ → Regex
            Some(Token::Regex(pattern)) => {
                self.advance(); // consume regex token
                Matcher::Regex(pattern)
            }

            // bare Str → Contains (default)
            Some(Token::Str(s)) => {
                self.advance(); // consume string
                Matcher::Contains(s)
            }

            other => {
                return Err(DslError::ParseError(format!(
                    "unexpected token in predicate argument: {other:?}"
                )))
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
pub(crate) fn parse(tokens: Vec<Token>) -> Result<Expr, DslError> {
    if tokens.is_empty() {
        return Err(DslError::EmptyExpression);
    }
    let mut parser = Parser::new(tokens);
    let expr = parser.parse_expr()?;
    if parser.pos < parser.tokens.len() {
        return Err(DslError::ParseError(format!(
            "unexpected token {:?}",
            parser.tokens[parser.pos]
        )));
    }
    Ok(expr)
}

// ── Evaluator ─────────────────────────────────────────────────────────────────

/// Evaluate an [`Expr`] against a [`crate::query::resource::QueryEntry`].
///
/// Returns `true` if the entry matches the expression.
pub(crate) fn eval(expr: &Expr, entry: &crate::query::resource::QueryEntry) -> bool {
    match expr {
        Expr::And(a, b) => eval(a, entry) && eval(b, entry),
        Expr::Or(a, b) => eval(a, entry) || eval(b, entry),
        Expr::Not(inner) => !eval(inner, entry),
        Expr::Predicate { name, matcher } => {
            let Some(field_val) = entry.get(name) else {
                return false;
            };
            match matcher {
                Matcher::Any => !field_val.is_empty() && field_val != "false",
                Matcher::Contains(s) => field_val.split(',').any(|v| v.trim().contains(s.as_str())),
                Matcher::Exact(s) => field_val.split(',').any(|v| v.trim() == s.as_str()),
                Matcher::Regex(pattern) => {
                    // Compile at eval time; errors are surfaced via validate_predicates
                    // before eval is called in production, so unwrap is safe here for
                    // patterns that passed validation.
                    match Regex::new(pattern) {
                        Ok(re) => field_val.split(',').any(|v| re.is_match(v.trim())),
                        Err(_) => false,
                    }
                }
            }
        }
    }
}

/// Walk the expression tree and validate that every predicate name is allowed
/// for the given resource kind.
///
/// # Errors
///
/// Returns [`DslError::InvalidPredicate`] for the first invalid predicate found,
/// or [`DslError::InvalidRegex`] if a regex pattern fails to compile.
pub(crate) fn validate_predicates(
    expr: &Expr,
    resource: &crate::query::resource::ResourceKind,
) -> Result<(), DslError> {
    match expr {
        Expr::And(a, b) | Expr::Or(a, b) => {
            validate_predicates(a, resource)?;
            validate_predicates(b, resource)?;
            Ok(())
        }
        Expr::Not(inner) => validate_predicates(inner, resource),
        Expr::Predicate { name, matcher } => {
            let valid = resource.valid_predicates();
            if !valid.contains(&name.as_str()) {
                return Err(DslError::InvalidPredicate {
                    predicate: name.clone(),
                    resource: resource.as_str().to_string(),
                });
            }
            // Also validate regex patterns compile
            if let Matcher::Regex(pattern) = matcher {
                Regex::new(pattern)
                    .map_err(|e| DslError::InvalidRegex(pattern.clone(), e.to_string()))?;
            }
            Ok(())
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::query::resource::{QueryEntry, ResourceKind};
    use std::collections::HashMap;

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
        assert_eq!(lex(r#"name("foo)"#), Err(DslError::UnterminatedString));
    }

    #[test]
    fn lex_error_unterminated_regex() {
        assert_eq!(lex("name(/foo)"), Err(DslError::UnterminatedRegex));
    }

    // ── Parser tests ──────────────────────────────────────────────────────────

    fn lex_and_parse(input: &str) -> Result<Expr, DslError> {
        let tokens = lex(input)?;
        parse(tokens)
    }

    #[test]
    fn parse_simple_predicate() {
        let expr = lex_and_parse("name(foo)").unwrap();
        assert_eq!(
            expr,
            Expr::Predicate {
                name: "name".into(),
                matcher: Matcher::Contains("foo".into()),
            }
        );
    }

    #[test]
    fn parse_exact_matcher() {
        let expr = lex_and_parse("name(=exact)").unwrap();
        assert_eq!(
            expr,
            Expr::Predicate {
                name: "name".into(),
                matcher: Matcher::Exact("exact".into()),
            }
        );
    }

    #[test]
    fn parse_contains_matcher() {
        let expr = lex_and_parse("name(~partial)").unwrap();
        assert_eq!(
            expr,
            Expr::Predicate {
                name: "name".into(),
                matcher: Matcher::Contains("partial".into()),
            }
        );
    }

    #[test]
    fn parse_regex_matcher() {
        let expr = lex_and_parse("name(/test_.*/)").unwrap();
        assert_eq!(
            expr,
            Expr::Predicate {
                name: "name".into(),
                matcher: Matcher::Regex("test_.*".into()),
            }
        );
    }

    #[test]
    fn parse_boolean_predicate() {
        let expr = lex_and_parse("async()").unwrap();
        assert_eq!(
            expr,
            Expr::Predicate {
                name: "async".into(),
                matcher: Matcher::Any,
            }
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
        assert_eq!(lex_and_parse(""), Err(DslError::EmptyExpression));
    }

    #[test]
    fn parse_error_unmatched_paren() {
        assert!(matches!(
            lex_and_parse("(a(x)"),
            Err(DslError::UnmatchedParen | DslError::ParseError(_))
        ));
    }

    #[test]
    fn parse_error_missing_parens_on_predicate() {
        // "name" without parens — token stream is just [Ident("name")]
        // parse_predicate sees no LParen → MissingParens
        let result = lex_and_parse("name");
        assert!(
            matches!(result, Err(DslError::MissingParens(_))),
            "expected MissingParens, got {result:?}"
        );
    }

    // ── Evaluator tests ───────────────────────────────────────────────────────

    fn entry(pairs: &[(&str, &str)]) -> QueryEntry {
        let fields: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        QueryEntry { fields }
    }

    fn eval_str(input: &str, e: &QueryEntry) -> bool {
        let expr = lex_and_parse(input).unwrap();
        eval(&expr, e)
    }

    #[test]
    fn eval_contains_match() {
        assert!(eval_str("name(foo)", &entry(&[("name", "foobar")])));
    }

    #[test]
    fn eval_contains_no_match() {
        assert!(!eval_str("name(foo)", &entry(&[("name", "bar")])));
    }

    #[test]
    fn eval_exact_match() {
        assert!(eval_str("name(=foo)", &entry(&[("name", "foo")])));
    }

    #[test]
    fn eval_exact_no_match() {
        assert!(!eval_str("name(=foo)", &entry(&[("name", "foobar")])));
    }

    #[test]
    fn eval_regex_match() {
        assert!(eval_str(
            "name(/^test_/)",
            &entry(&[("name", "test_something")])
        ));
    }

    #[test]
    fn eval_boolean_true() {
        assert!(eval_str("async()", &entry(&[("async", "true")])));
    }

    #[test]
    fn eval_boolean_false() {
        assert!(!eval_str("async()", &entry(&[("async", "false")])));
    }

    #[test]
    fn eval_boolean_missing_field() {
        assert!(!eval_str("async()", &entry(&[])));
    }

    #[test]
    fn eval_and() {
        let e = entry(&[("name", "foo"), ("mark", "slow")]);
        assert!(eval_str("name(foo) & mark(slow)", &e));
        assert!(!eval_str("name(foo) & mark(fast)", &e));
    }

    #[test]
    fn eval_and_short_circuit() {
        // Missing field means false; AND short-circuits
        let e = entry(&[("name", "foo")]);
        assert!(!eval_str("name(foo) & mark(slow)", &e));
    }

    #[test]
    fn eval_or() {
        let e = entry(&[("name", "foo")]);
        assert!(eval_str("name(foo) | mark(slow)", &e));
        assert!(!eval_str("name(bar) | mark(slow)", &e));
    }

    #[test]
    fn eval_not() {
        let e = entry(&[("name", "foo")]);
        assert!(!eval_str("!name(foo)", &e));
        assert!(eval_str("!name(bar)", &e));
    }

    #[test]
    fn eval_multi_value_contains() {
        // field "mark" has two comma-separated values
        let e = entry(&[("mark", "slow,integration")]);
        assert!(eval_str("mark(~slow)", &e));
        assert!(eval_str("mark(~integration)", &e));
        assert!(!eval_str("mark(~fast)", &e));
    }

    #[test]
    fn eval_multi_value_exact() {
        let e = entry(&[("mark", "slow,integration")]);
        assert!(eval_str("mark(=slow)", &e));
        assert!(eval_str("mark(=integration)", &e));
        assert!(!eval_str("mark(=slo)", &e));
    }

    // ── Validation tests ──────────────────────────────────────────────────────

    #[test]
    fn validate_valid_predicate() {
        let expr = lex_and_parse("name(foo)").unwrap();
        assert!(validate_predicates(&expr, &ResourceKind::Tests).is_ok());
    }

    #[test]
    fn validate_invalid_predicate() {
        // "shared" is valid for Fixtures but not for Tests
        let expr = lex_and_parse("shared()").unwrap();
        assert!(matches!(
            validate_predicates(&expr, &ResourceKind::Tests),
            Err(DslError::InvalidPredicate { .. })
        ));
    }

    #[test]
    fn validate_shared_valid_for_fixtures() {
        let expr = lex_and_parse("shared()").unwrap();
        assert!(validate_predicates(&expr, &ResourceKind::Fixtures).is_ok());
    }

    #[test]
    fn validate_protocol_valid_for_plugins() {
        let expr = lex_and_parse("protocol(reporter)").unwrap();
        assert!(validate_predicates(&expr, &ResourceKind::Plugins).is_ok());
    }
}
