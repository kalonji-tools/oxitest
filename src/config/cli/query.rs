use camino::Utf8PathBuf;

use super::super::ColorMode;
use crate::query::resource::ResourceKind;

/// Arguments for `oxitest query`.
#[derive(clap::Args, Debug, Clone)]
#[command(after_help = "\
RESOURCES:
  tests      Test functions (instant)
  fixtures   Registered fixtures (requires Python)
  marks      Mark decorators (instant)
  plugins    Registered plugins (requires Python)

PREDICATES:
  name()     Primary identifier match         [all resources]
  source()   Source file path match           [all resources]
  mark()     Has matching mark                [tests]
  shared()   Has lifetime=\"module\"            [fixtures]
  autouse()  Is autouse fixture               [fixtures]
  async()    Is async                         [tests, fixtures]
  protocol() Implements protocol              [plugins]

MATCHERS:
  name(foo)       Contains 'foo' (default)
  name(=exact)    Exact match
  name(~partial)  Explicit contains
  name(/re.*/)    Regex match

EXAMPLES:
  oxitest query tests                        List all tests
  oxitest query tests -E 'mark(slow)'        Tests marked @slow
  oxitest query tests -E 'async() & !mark(skip)'
  oxitest query fixtures -E 'shared()'
  oxitest query tests --fzf                  Interactive fuzzy finder
  oxitest query tests --detail test_foo      Show details for test_foo
  oxitest query tests --jsonl                JSON lines output
  oxitest query tests --count                Just the count\
")]
pub struct QueryArgs {
    /// Resource type to query
    pub resource: ResourceKind,

    /// Filter expression (DSL)
    #[arg(short = 'E', value_name = "EXPR")]
    pub expression: Option<String>,

    /// Interactive fuzzy finder
    #[arg(long)]
    pub fzf: bool,

    /// Show a single-item detail card for the given identifier
    #[arg(long, value_name = "ID")]
    pub detail: Option<String>,

    /// Output results as JSON lines (one object per entry).
    #[arg(long)]
    pub jsonl: bool,

    /// Show only the count
    #[arg(long)]
    pub count: bool,

    /// Show fixture dependency tree (fixtures only)
    #[arg(long)]
    pub tree: bool,

    /// Color output mode: auto, always, never
    #[arg(long, value_enum, help_heading = "Output")]
    pub color: Option<ColorMode>,

    /// Paths to test files or directories
    pub paths: Vec<Utf8PathBuf>,
}

#[cfg(test)]
impl QueryArgs {
    #[expect(dead_code, reason = "test helper not yet exercised by any test")]
    pub fn default_for_test() -> Self {
        use clap::Parser;
        let parsed = super::OxitestCli::try_parse_from(["oxitest", "query", "tests"])
            .expect("default QueryArgs must parse");
        match parsed.command {
            Some(super::Command::Query(args)) => args,
            other => panic!("`oxitest query tests` must parse as Command::Query, got {other:?}"),
        }
    }
}

/// Arguments for the deprecated `oxitest fixtures` subcommand.
///
/// This subcommand is kept for backwards compatibility but delegates
/// to `oxitest query fixtures`. Use `oxitest query fixtures` instead.
#[derive(clap::Args, Debug, Clone)]
pub struct FixturesArgs {
    /// Paths to test files or directories
    pub paths: Vec<Utf8PathBuf>,
}
