//! Shared types for the query DSL: tokens, matchers, expression tree, errors.

// ── Token ──────────────────────────────────────────────────────────────────────

/// Tokens produced by [`super::compile::lex`].
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
