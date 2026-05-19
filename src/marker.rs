//! Marker expression parser and evaluator for the `-m` flag.
//!
//! Parses boolean expressions like `slow and not integration` using a
//! [`Logos`]-based lexer and recursive descent parser. Evaluates expressions
//! against the set of marker names attached to each test item.

use logos::Logos;

use crate::types::TestItem;

// ── Token ─────────────────────────────────────────────────────────────────────

#[derive(Logos, Debug, PartialEq, Clone)]
#[logos(skip r"[ \t\r\n\f]+")] // skip whitespace between tokens
enum Token {
    #[token("and")]
    And,
    #[token("or")]
    Or,
    #[token("not")]
    Not,
    #[token("(")]
    LParen,
    #[token(")")]
    RParen,
    /// Any identifier: marker name or keyword not matched above.
    #[regex(r"[a-zA-Z_][a-zA-Z0-9_]*", |lex| lex.slice().to_owned())]
    Ident(String),
}

impl std::fmt::Display for Token {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Token::And => write!(f, "and"),
            Token::Or => write!(f, "or"),
            Token::Not => write!(f, "not"),
            Token::LParen => write!(f, "("),
            Token::RParen => write!(f, ")"),
            Token::Ident(s) => write!(f, "{}", s),
        }
    }
}

// ── Marker expression AST ─────────────────────────────────────────────────────

enum MarkerExpr {
    Ident(String),
    Not(Box<MarkerExpr>),
    And(Box<MarkerExpr>, Box<MarkerExpr>),
    Or(Box<MarkerExpr>, Box<MarkerExpr>),
}

fn eval_marker(expr: &MarkerExpr, markers: &[String]) -> bool {
    match expr {
        MarkerExpr::Ident(name) => markers.iter().any(|m| m == name),
        MarkerExpr::Not(inner) => !eval_marker(inner, markers),
        MarkerExpr::And(a, b) => eval_marker(a, markers) && eval_marker(b, markers),
        MarkerExpr::Or(a, b) => eval_marker(a, markers) || eval_marker(b, markers),
    }
}

// ── Recursive-descent parser ──────────────────────────────────────────────────

struct MarkerParser {
    tokens: Vec<Token>,
    pos: usize,
}

impl MarkerParser {
    fn new(s: &str) -> Result<Self, String> {
        let mut tokens = Vec::new();
        for (result, span) in Token::lexer(s).spanned() {
            match result {
                Ok(tok) => tokens.push(tok),
                Err(()) => {
                    return Err(format!(
                        "invalid character at position {} in marker expression: {:?}",
                        span.start, s
                    ))
                }
            }
        }
        Ok(Self { tokens, pos: 0 })
    }

    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.pos)
    }

    fn advance(&mut self) {
        self.pos += 1;
    }

    fn parse_expr(&mut self) -> Result<MarkerExpr, String> {
        self.parse_or()
    }

    fn parse_or(&mut self) -> Result<MarkerExpr, String> {
        let mut left = self.parse_and()?;
        while self.peek() == Some(&Token::Or) {
            self.advance();
            let right = self.parse_and()?;
            left = MarkerExpr::Or(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn parse_and(&mut self) -> Result<MarkerExpr, String> {
        let mut left = self.parse_not()?;
        while self.peek() == Some(&Token::And) {
            self.advance();
            let right = self.parse_not()?;
            left = MarkerExpr::And(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn parse_not(&mut self) -> Result<MarkerExpr, String> {
        if self.peek() == Some(&Token::Not) {
            self.advance();
            let inner = self.parse_not()?;
            return Ok(MarkerExpr::Not(Box::new(inner)));
        }
        self.parse_atom()
    }

    fn parse_atom(&mut self) -> Result<MarkerExpr, String> {
        if self.peek() == Some(&Token::LParen) {
            self.advance();
            let inner = self.parse_expr()?;
            if self.peek() != Some(&Token::RParen) {
                return Err("expected ')'".to_string());
            }
            self.advance();
            return Ok(inner);
        }
        match self.tokens.get(self.pos).cloned() {
            Some(Token::Ident(name)) => {
                self.advance();
                Ok(MarkerExpr::Ident(name))
            }
            _ => Err("expected marker name".to_string()),
        }
    }
}

// ── Public interface ──────────────────────────────────────────────────────────

pub fn filter_by_marker_expr(items: Vec<TestItem>, expr: &str) -> Result<Vec<TestItem>, String> {
    let mut parser = MarkerParser::new(expr)?;
    let ast = parser.parse_expr()?;
    if parser.pos < parser.tokens.len() {
        return Err(format!("unexpected token '{}'", parser.tokens[parser.pos]));
    }
    Ok(items
        .into_iter()
        .filter(|item| eval_marker(&ast, &item.markers))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8PathBuf;

    fn make_marked(name: &str, markers: Vec<&str>) -> TestItem {
        TestItem {
            node_id: crate::types::NodeId::new("tests/test_mod.py", name, None),
            module_path: Utf8PathBuf::from("tests/test_mod.py"),
            fn_name: name.to_string(),
            lineno: 0,
            markers: markers.into_iter().map(|s| s.to_string()).collect(),
            param_id: None,
            param_values: vec![],
        }
    }

    #[test]
    fn test_marker_filter_simple_match() {
        let items = vec![
            make_marked("test_a", vec!["slow"]),
            make_marked("test_b", vec![]),
        ];
        let result = filter_by_marker_expr(items, "slow").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].fn_name, "test_a");
    }

    #[test]
    fn test_marker_filter_not() {
        let items = vec![
            make_marked("test_a", vec!["slow"]),
            make_marked("test_b", vec![]),
        ];
        let result = filter_by_marker_expr(items, "not slow").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].fn_name, "test_b");
    }

    #[test]
    fn test_marker_filter_and() {
        let items = vec![
            make_marked("test_a", vec!["slow", "integration"]),
            make_marked("test_b", vec!["slow"]),
        ];
        let result = filter_by_marker_expr(items, "slow and integration").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].fn_name, "test_a");
    }

    #[test]
    fn test_marker_filter_or() {
        let items = vec![
            make_marked("test_a", vec!["slow"]),
            make_marked("test_b", vec!["integration"]),
            make_marked("test_c", vec![]),
        ];
        let result = filter_by_marker_expr(items, "slow or integration").unwrap();
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_marker_filter_parens() {
        let items = vec![
            make_marked("test_a", vec!["slow", "unit"]),
            make_marked("test_b", vec!["slow", "integration"]),
            make_marked("test_c", vec!["unit"]),
        ];
        let result = filter_by_marker_expr(items, "slow and (unit or integration)").unwrap();
        assert_eq!(result.len(), 2);
        let names: Vec<_> = result.iter().map(|i| i.fn_name.as_str()).collect();
        assert!(names.contains(&"test_a"));
        assert!(names.contains(&"test_b"));
    }

    #[test]
    fn test_marker_filter_no_match_returns_empty() {
        let items = vec![make_marked("test_a", vec!["slow"])];
        let result = filter_by_marker_expr(items, "integration").unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_marker_filter_invalid_expr_returns_err() {
        let items = vec![make_marked("test_a", vec!["slow"])];
        assert!(filter_by_marker_expr(items, "(unclosed").is_err());
    }

    #[test]
    fn test_marker_filter_empty_expr_is_parse_error() {
        // An empty string causes a parse error (no token available)
        let items = vec![make_marked("test_a", vec![])];
        assert!(filter_by_marker_expr(items, "").is_err());
    }

    #[test]
    fn test_marker_filter_trailing_token_returns_err() {
        let items = vec![make_marked("test_a", vec!["slow"])];
        assert!(filter_by_marker_expr(items, "slow extra").is_err());
    }

    #[test]
    fn test_marker_filter_keyword_as_marker_name_is_parse_error() {
        // "and", "or", "not" are reserved keywords in the expression language.
        // Marker names that collide with keywords produce a parse error.
        // (pytest discourages using reserved words as marker names.)
        let items = vec![make_marked("test_a", vec!["and"])];
        assert!(filter_by_marker_expr(items, "and").is_err());
    }
}
