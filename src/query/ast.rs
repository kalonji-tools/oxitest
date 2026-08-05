//! Shared types for the query DSL: tokens, matchers, expression tree, errors.

// ── Token ──────────────────────────────────────────────────────────────────────

/// Tokens produced by [`super::compile::lex`].
#[derive(Debug, PartialEq, Clone)]
pub enum Token {
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
#[derive(thiserror::Error, Debug, miette::Diagnostic)]
pub enum DslError {
    /// A string literal was not properly terminated.
    #[error("unterminated string literal")]
    #[diagnostic(help("close the string with a matching quote character"))]
    UnterminatedString {
        #[label("string starts here")]
        span: miette::SourceSpan,
    },
    /// A regex literal was not properly terminated.
    #[error("unterminated regex literal")]
    #[diagnostic(help("close the regex with a matching '/'"))]
    UnterminatedRegex {
        #[label("regex starts here")]
        span: miette::SourceSpan,
    },
    /// The expression string is empty.
    #[error("empty expression")]
    EmptyExpression,
    /// An unmatched closing parenthesis was encountered.
    #[error("unmatched ')'")]
    UnmatchedParen,
    /// A predicate was missing its `(...)` argument list.
    #[error("expected '(' after predicate name '{name}'")]
    #[diagnostic(help("predicates require parentheses: {name}() or {name}(value)"))]
    MissingParens { name: String },
    /// A general parse error with a message.
    #[error("parse error: {message}")]
    ParseError { message: String },
    /// A predicate name is not valid for the given resource kind.
    #[error("predicate '{predicate}' is not valid for resource '{resource}'")]
    #[diagnostic(help("valid predicates: name, source, mark, async"))]
    InvalidPredicate {
        /// The predicate name used.
        predicate: String,
        /// The resource kind name.
        resource: String,
    },
    /// A regex pattern failed to compile.
    #[error("invalid regex pattern '{pattern}': {reason}")]
    InvalidRegex { pattern: String, reason: String },
}

// ── AST ───────────────────────────────────────────────────────────────────────

/// How a predicate argument matches a field value.
#[derive(Debug, Clone)]
pub enum Matcher {
    /// Field exists and is non-empty and non-"false" (boolean predicate).
    Any,
    /// Any comma-separated value contains the string (substring match).
    Contains(String),
    /// Any comma-separated value exactly equals the string.
    Exact(String),
    /// Any comma-separated value matches the regex pattern (pre-compiled at parse time).
    Regex(regex::Regex),
}

/// A parsed DSL expression tree.
#[derive(Debug, Clone)]
pub enum Expr {
    /// A single predicate: `field_name(matcher)`.
    Predicate {
        /// The field name to look up.
        name: String,
        /// The matcher to apply.
        matcher: Matcher,
    },
    /// Logical AND of two sub-expressions.
    And(Box<Self>, Box<Self>),
    /// Logical OR of two sub-expressions.
    Or(Box<Self>, Box<Self>),
    /// Logical NOT of a sub-expression.
    Not(Box<Self>),
}
