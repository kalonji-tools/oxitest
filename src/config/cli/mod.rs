use camino::Utf8PathBuf;
use clap::Parser;

mod debug;
pub use debug::*;

mod enums;
pub use enums::*;

mod inspect;
pub use inspect::*;

mod query;
pub use query::*;

mod run;
pub use run::*;

mod plugin;
pub use plugin::{add_plugin_args, extract_plugin_values};

mod shared;
pub use shared::*;

// ── Command enum ─────────────────────────────────────────────────────────────

/// Available subcommands.
#[derive(clap::Subcommand, Debug, Clone)]
pub enum Command {
    /// Run tests (default when no subcommand is given)
    Run(RunArgs),
    /// Interactive debugger session
    Debug(DebugArgs),
    /// Query tests, fixtures, marks, or plugins
    Query(QueryArgs),
    /// Interactive TUI for test suite introspection
    Inspect(InspectArgs),
    /// Print environment information (version, Python, rustc, OS)
    Env,
    /// Generate shell completions
    #[command(hide = true)]
    Completions {
        /// Shell to generate completions for
        #[arg(value_enum)]
        shell: clap_complete::Shell,
    },
    /// List registered fixtures (deprecated — use `oxitest query fixtures`)
    #[command(hide = true)]
    Fixtures(FixturesArgs),
}

// ── partition_positionals ─────────────────────────────────────────────────────

/// Split raw positional args into plain paths and node IDs.
///
/// An arg containing `::` is a node ID. The file path is extracted by splitting
/// on the first `::`. Each node ID's parent file path is added to the returned
/// paths vector (deduped).
///
/// An arg without `::` is a plain path.
pub(crate) fn partition_positionals(
    raw: Vec<Utf8PathBuf>,
) -> (Vec<Utf8PathBuf>, Vec<crate::types::NodeId>) {
    let mut paths: Vec<Utf8PathBuf> = Vec::new();
    let mut node_ids = Vec::new();

    for arg in raw {
        let arg_str = arg.as_str();
        let (file_part, chain) = crate::types::node_id::split_node_id_str(arg_str);
        if !chain.is_empty() {
            // Any :: in the arg — treat as a node ID, matching pre-refactor
            // behavior. Empty-segment / malformed cases fall through to
            // downstream validation which surfaces "invalid node ID" errors.
            node_ids.push(crate::types::NodeId::from_raw(arg_str));
            // Only extract the path for file collection if it has no glob chars.
            // Glob paths (e.g. "tests/test_*.py") are not real files.
            if !crate::filter::contains_glob_chars(file_part) {
                let file_path = Utf8PathBuf::from(file_part);
                if !paths.contains(&file_path) {
                    paths.push(file_path);
                }
            }
        } else if !paths.contains(&arg) {
            paths.push(arg);
        }
    }

    (paths, node_ids)
}

// ── Top-level parser ─────────────────────────────────────────────────────────

/// oxitest — a fast Python test runner.
#[derive(Parser, Debug)]
#[command(name = "oxitest", version, about = "A fast Python test runner")]
pub struct OxitestCli {
    /// Disable .gitignore-aware file filtering
    #[arg(long)]
    pub no_use_gitignore: bool,

    #[command(subcommand)]
    pub command: Option<Command>,
}

/// Partition positional args into paths and node IDs for Run and Debug commands.
fn partition_command(cmd: &mut Command) {
    match cmd {
        Command::Run(args) => {
            let (paths, node_ids) = partition_positionals(std::mem::take(&mut args.paths));
            args.paths = paths;
            args.node_ids = node_ids;
        }
        Command::Debug(args) => {
            let (paths, node_ids) = partition_positionals(std::mem::take(&mut args.paths));
            args.paths = paths;
            args.node_ids = node_ids;
        }
        Command::Query(_)
        | Command::Inspect(_)
        | Command::Env
        | Command::Completions { .. }
        | Command::Fixtures(_) => {}
    }
}

impl OxitestCli {
    /// Parse arguments, falling back to implicit `run` when no subcommand is given.
    ///
    /// Returns `(Command, use_gitignore)` where `use_gitignore` is `true` unless
    /// `--no-use-gitignore` was passed.
    ///
    /// Tries parsing with subcommands first. On failure (e.g. `oxitest tests/ -E "name(foo)"`
    /// without an explicit `run`), falls back to parsing all args as `RunArgs`.
    pub fn resolve(args: &[String]) -> Result<(Command, bool), clap::Error> {
        // First, try normal parsing with subcommands.
        match Self::try_parse_from(args) {
            Ok(cli) => {
                let use_gitignore = !cli.no_use_gitignore;
                if let Some(mut cmd) = cli.command {
                    partition_command(&mut cmd);
                    return Ok((cmd, use_gitignore));
                }
                // No subcommand given (bare `oxitest`) — treat as `run` with no args.
                let run_args: Vec<&str> = vec![&args[0], "run"];
                // (no extra args to forward)
                let cli = Self::try_parse_from(&run_args)?;
                // `run` is in `run_args`, so clap yields `Some` — but ADR-0011
                // bans unwrapping on that claim. A `None` is a usage error like
                // any other, not a reason to abort the process.
                let Some(mut cmd) = cli.command else {
                    use clap::CommandFactory;
                    return Err(Self::command().error(
                        clap::error::ErrorKind::MissingSubcommand,
                        "bare `oxitest` could not be resolved to `oxitest run`",
                    ));
                };
                partition_command(&mut cmd);
                Ok((cmd, use_gitignore))
            }
            Err(e) => {
                // Display-only requests (--version, --help) should propagate immediately
                // so the caller can print them and exit 0.
                if e.kind() == clap::error::ErrorKind::DisplayVersion
                    || e.kind() == clap::error::ErrorKind::DisplayHelp
                    || e.kind() == clap::error::ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand
                {
                    return Err(e);
                }

                // If the initial parse failed, try inserting "run" to handle implicit default
                // subcommand (e.g. `oxitest tests/ -E "name(foo)"` or
                // `oxitest --no-use-gitignore tests/`).
                //
                // Top-level (global) flags like `--no-use-gitignore` can appear anywhere in
                // the args. We extract them, place them before the inserted "run" subcommand,
                // and pass the rest as subcommand args.
                if args.len() > 1 {
                    let global_flags: &[&str] = &["--no-use-gitignore"];
                    let mut run_args: Vec<&str> = Vec::with_capacity(args.len() + 1);
                    run_args.push(&args[0]);
                    // Extract global flags from anywhere in args[1..]
                    for arg in &args[1..] {
                        if global_flags.contains(&arg.as_str()) {
                            run_args.push(arg);
                        }
                    }
                    run_args.push("run");
                    // Then the rest (non-global args) in original order
                    for arg in &args[1..] {
                        if !global_flags.contains(&arg.as_str()) {
                            run_args.push(arg);
                        }
                    }
                    match Self::try_parse_from(&run_args) {
                        // `None` cannot happen — `run_args` contains "run" —
                        // but per ADR-0011 the claim is not worth an abort:
                        // the original error is the better message anyway.
                        Ok(cli) => match cli.command {
                            Some(mut cmd) => {
                                partition_command(&mut cmd);
                                Ok((cmd, !cli.no_use_gitignore))
                            }
                            None => Err(e),
                        },
                        Err(_) => Err(e), // Return the original error for better UX
                    }
                } else {
                    Err(e)
                }
            }
        }
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::FailedMode;

    /// Convert a fixed-size array of `&str` into `Vec<String>`.
    fn s<const N: usize>(args: [&str; N]) -> Vec<String> {
        args.iter().map(|a| a.to_string()).collect()
    }

    // ── OxitestCli::resolve ───────────────────────────────────────────────────

    #[test]
    fn bare_oxitest_defaults_to_run() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
    }

    #[test]
    fn explicit_run_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
    }

    #[test]
    fn implicit_run_with_path() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "tests/"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/")]);
    }

    #[test]
    fn implicit_run_with_flags() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "-E", "name(foo)"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.filter.expression.as_deref(), Some("name(foo)"));
    }

    #[test]
    fn implicit_run_with_path_and_flags() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "tests/", "-E", "name(foo)"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/")]);
        assert_eq!(args.filter.expression.as_deref(), Some("name(foo)"));
    }

    #[test]
    fn debug_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug"])).unwrap();
        assert!(matches!(cmd, Command::Debug(_)));
    }

    #[test]
    fn debug_with_always() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "debug", "--always"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert!(args.always);
    }

    #[test]
    fn env_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "env"])).unwrap();
        assert!(matches!(cmd, Command::Env));
    }

    // ── Query subcommand tests ────────────────────────────────────────────────

    #[test]
    fn query_tests_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests"])).unwrap();
        assert!(matches!(cmd, Command::Query(_)));
    }

    #[test]
    fn query_fixtures_subcommand() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "fixtures"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(
            args.resource,
            crate::query::resource::ResourceKind::Fixtures
        );
    }

    #[test]
    fn query_with_expression() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "query", "tests", "-E", "name(~foo)"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(args.expression.as_deref(), Some("name(~foo)"));
    }

    #[test]
    fn query_with_fzf() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests", "--fzf"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.fzf);
    }

    #[test]
    fn query_with_detail() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "query", "tests", "--detail", "test_foo"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert_eq!(args.detail.as_deref(), Some("test_foo"));
    }

    #[test]
    fn query_with_format_jsonl() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests", "--jsonl"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.jsonl, "expected --jsonl flag to be set");
    }

    #[test]
    fn query_with_count() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "tests", "--count"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.count);
    }

    #[test]
    fn query_with_tree() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "query", "fixtures", "--tree"])).unwrap();
        let Command::Query(args) = cmd else {
            panic!("expected Command::Query");
        };
        assert!(args.tree);
    }

    // ── Inspect subcommand tests ───────────────────────────────────────────────

    #[test]
    fn inspect_args_parses_name() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "inspect", "test_foo"])).unwrap();
        let Command::Inspect(args) = cmd else {
            panic!("expected Command::Inspect");
        };
        assert_eq!(args.name.as_deref(), Some("test_foo"));
    }

    #[test]
    fn inspect_args_parses_expression() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "inspect", "-E", "mark:slow"])).unwrap();
        let Command::Inspect(args) = cmd else {
            panic!("expected Command::Inspect");
        };
        assert_eq!(args.filter.expression.as_deref(), Some("mark:slow"));
    }

    #[test]
    fn inspect_args_parses_affected() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "inspect", "--affected"])).unwrap();
        let Command::Inspect(args) = cmd else {
            panic!("expected Command::Inspect");
        };
        assert!(args.filter.affected.is_some());
    }

    #[test]
    fn inspect_args_parses_lf() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "inspect", "--lf"])).unwrap();
        let Command::Inspect(args) = cmd else {
            panic!("expected Command::Inspect");
        };
        assert_eq!(args.failed_filter.resolve(), Some(FailedMode::Only));
    }

    // ── --no-use-gitignore tests ──────────────────────────────────────────────

    #[test]
    fn no_use_gitignore_flag() {
        let (cmd, use_gi) =
            OxitestCli::resolve(&s(["oxitest", "--no-use-gitignore", "run"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
        assert!(!use_gi);
    }

    #[test]
    fn use_gitignore_default_is_true() {
        let (_, use_gi) = OxitestCli::resolve(&s(["oxitest", "run"])).unwrap();
        assert!(use_gi);
    }

    #[test]
    fn no_use_gitignore_with_implicit_run() {
        let (cmd, use_gi) =
            OxitestCli::resolve(&s(["oxitest", "--no-use-gitignore", "tests/"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
        assert!(!use_gi);
    }

    #[test]
    fn no_use_gitignore_after_path_implicit_run() {
        // Simulates how run_oxitest helper calls it: path first, flag after
        let (cmd, use_gi) =
            OxitestCli::resolve(&s(["oxitest", "tests/", "--no-use-gitignore"])).unwrap();
        assert!(matches!(cmd, Command::Run(_)));
        assert!(!use_gi);
    }

    // ── partition_positionals ───────────────────────────────────────────────

    #[test]
    fn partition_plain_paths_only() {
        let (paths, ids) = super::partition_positionals(vec![
            Utf8PathBuf::from("tests/test_a.py"),
            Utf8PathBuf::from("tests/test_b.py"),
        ]);
        assert_eq!(paths.len(), 2);
        assert!(ids.is_empty());
    }

    #[test]
    fn partition_node_ids_only() {
        let (paths, ids) = super::partition_positionals(vec![
            Utf8PathBuf::from("tests/test_a.py::test_foo"),
            Utf8PathBuf::from("tests/test_b.py::test_bar"),
        ]);
        assert_eq!(paths.len(), 2);
        assert_eq!(ids.len(), 2);
        assert_eq!(ids[0].as_ref(), "tests/test_a.py::test_foo");
        assert!(paths.contains(&Utf8PathBuf::from("tests/test_a.py")));
        assert!(paths.contains(&Utf8PathBuf::from("tests/test_b.py")));
    }

    #[test]
    fn partition_mixed_paths_and_node_ids() {
        let (paths, ids) = super::partition_positionals(vec![
            Utf8PathBuf::from("tests/test_a.py::test_foo"),
            Utf8PathBuf::from("tests/test_b.py"),
        ]);
        assert_eq!(paths.len(), 2);
        assert_eq!(ids.len(), 1);
        assert!(paths.contains(&Utf8PathBuf::from("tests/test_a.py")));
        assert!(paths.contains(&Utf8PathBuf::from("tests/test_b.py")));
    }

    #[test]
    fn partition_deduplicates_extracted_paths() {
        let (paths, ids) = super::partition_positionals(vec![
            Utf8PathBuf::from("tests/test_a.py::test_foo"),
            Utf8PathBuf::from("tests/test_a.py::test_bar"),
        ]);
        assert_eq!(
            paths,
            vec![Utf8PathBuf::from("tests/test_a.py")],
            "parent path should appear once"
        );
        assert_eq!(ids.len(), 2);
    }

    #[test]
    fn partition_class_node_id_splits_on_first_separator() {
        let (paths, ids) = super::partition_positionals(vec![Utf8PathBuf::from(
            "tests/test_cls.py::TestSuite::test_foo",
        )]);
        assert_eq!(
            paths,
            vec![Utf8PathBuf::from("tests/test_cls.py")],
            "must split on first :: to get file path"
        );
        assert_eq!(ids[0].as_ref(), "tests/test_cls.py::TestSuite::test_foo");
    }

    #[test]
    fn partition_empty_input() {
        let (paths, ids) = super::partition_positionals(vec![]);
        assert!(paths.is_empty());
        assert!(ids.is_empty());
    }

    #[test]
    fn partition_glob_node_id_does_not_extract_path() {
        let (paths, ids) =
            super::partition_positionals(vec![Utf8PathBuf::from("tests/test_*.py::test_foo")]);
        assert!(
            paths.is_empty(),
            "glob path should not be extracted: {paths:?}"
        );
        assert_eq!(ids.len(), 1);
        assert_eq!(ids[0].as_ref(), "tests/test_*.py::test_foo");
    }

    #[test]
    fn partition_mixed_glob_and_literal_node_ids() {
        let (paths, ids) = super::partition_positionals(vec![
            Utf8PathBuf::from("tests/test_a.py::test_foo"),
            Utf8PathBuf::from("tests/test_*.py::test_bar"),
        ]);
        assert_eq!(paths.len(), 1);
        assert_eq!(paths[0], Utf8PathBuf::from("tests/test_a.py"));
        assert_eq!(ids.len(), 2);
    }

    #[test]
    fn partition_glob_in_test_name_still_extracts_path() {
        let (paths, ids) =
            super::partition_positionals(vec![Utf8PathBuf::from("tests/test_a.py::test_f*")]);
        assert_eq!(paths.len(), 1);
        assert_eq!(paths[0], Utf8PathBuf::from("tests/test_a.py"));
        assert_eq!(ids.len(), 1);
    }

    #[test]
    fn partition_positionals_malformed_node_id_still_classified_as_node_id() {
        // Regression test for the refactor in Task 1 of #1638. A trailing ::
        // (empty segment) is malformed but must still be treated as a NodeId
        // so downstream validation reports "invalid node ID" — not "file not
        // found: tests/test_a.py::".
        let (paths, node_ids) = partition_positionals(vec![Utf8PathBuf::from("tests/test_a.py::")]);
        assert_eq!(
            node_ids.len(),
            1,
            "trailing :: must classify as a NodeId, not a plain path — preserves pre-refactor CLI behavior",
        );
        assert_eq!(
            paths,
            vec![Utf8PathBuf::from("tests/test_a.py")],
            "file part before :: still gets extracted for file collection",
        );
    }

    // ── node_id integration tests ───────────────────────────────────────────

    #[test]
    fn run_with_node_id_positional() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "run", "tests/test_a.py::test_foo"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.node_ids.len(), 1);
        assert_eq!(args.node_ids[0].as_ref(), "tests/test_a.py::test_foo");
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/test_a.py")]);
    }

    #[test]
    fn implicit_run_with_node_id() {
        let (cmd, _) = OxitestCli::resolve(&s(["oxitest", "tests/test_a.py::test_foo"])).unwrap();
        let Command::Run(args) = cmd else {
            panic!("expected Command::Run");
        };
        assert_eq!(args.node_ids.len(), 1);
        assert_eq!(args.node_ids[0].as_ref(), "tests/test_a.py::test_foo");
    }

    #[test]
    fn debug_with_node_id_positional() {
        let (cmd, _) =
            OxitestCli::resolve(&s(["oxitest", "debug", "tests/test_a.py::test_foo"])).unwrap();
        let Command::Debug(args) = cmd else {
            panic!("expected Command::Debug");
        };
        assert_eq!(args.node_ids.len(), 1);
        assert_eq!(args.paths, vec![Utf8PathBuf::from("tests/test_a.py")]);
    }

    // ── Display requests (--version, --help) ─────────────────────────────────

    #[test]
    fn version_flag_returns_display_version_error() {
        let err = OxitestCli::resolve(&s(["oxitest", "--version"])).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::DisplayVersion);
    }

    #[test]
    fn short_version_flag_returns_display_version_error() {
        let err = OxitestCli::resolve(&s(["oxitest", "-V"])).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::DisplayVersion);
    }

    #[test]
    fn help_flag_returns_display_help_error() {
        let err = OxitestCli::resolve(&s(["oxitest", "--help"])).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::DisplayHelp);
    }

    #[test]
    fn version_flag_not_swallowed_by_implicit_run() {
        // --version must propagate as DisplayVersion, not be re-parsed as
        // an implicit "run" subcommand argument.
        let err = OxitestCli::resolve(&s(["oxitest", "--version"])).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::DisplayVersion);
        assert!(
            err.to_string().contains("oxitest"),
            "output should contain the program name"
        );
    }
}
