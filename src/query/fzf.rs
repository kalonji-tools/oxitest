//! fzf integration for interactive query browsing.
//!
//! Spawns fzf with the query results as input, configures preview and
//! keybindings for the given resource kind.

use std::io::Write;
use std::process::{Command, Stdio};

use crate::bridge;
use crate::config;

/// Launch fzf with the filtered query results as input.
///
/// Collects and filters entries using the same logic as [`super::run_query`],
/// then pipes the tab-delimited output to fzf with resource-appropriate
/// preview and keybindings. Returns `Ok(())` when fzf exits (including
/// user-cancelled), and `Err(msg)` on configuration or spawn errors.
pub(crate) fn run_fzf(
    py: pyo3::Python<'_>,
    args: &config::QueryArgs,
    test_files: &[camino::Utf8PathBuf],
    conftest_files: &[camino::Utf8PathBuf],
    session: Option<&bridge::FixtureSession>,
    cfg: &config::Config,
    _use_color: bool,
) -> Result<(), String> {
    // 1. Collect and filter entries (reuse existing logic)
    let expr = if let Some(ref expr_str) = args.expression {
        let tokens = super::dsl::lex(expr_str).map_err(|e| e.to_string())?;
        let parsed = super::dsl::parse(tokens).map_err(|e| e.to_string())?;
        super::dsl::validate_predicates(&parsed, &args.resource).map_err(|e| e.to_string())?;
        Some(parsed)
    } else {
        None
    };

    let entries =
        super::collect_entries(py, args.resource, test_files, conftest_files, session, cfg)?;

    let filtered: Vec<super::resource::QueryEntry> = if let Some(ref expr) = expr {
        entries
            .into_iter()
            .filter(|e| super::dsl::eval(expr, e))
            .collect()
    } else {
        entries
    };

    if filtered.is_empty() {
        return Err("no results to display".into());
    }

    // 2. Format as tab-delimited for fzf input
    let columns = super::default_columns(args.resource);
    let input = super::format::format_tab(&filtered, &columns);

    // 3. Build fzf command with preview and keybindings
    let resource_str = args.resource.as_str();
    let preview_cmd = format!("oxitest query {} --inspect {{1}}", resource_str);

    let mut fzf = Command::new("fzf")
        .stdin(Stdio::piped())
        .stdout(Stdio::inherit())
        .arg("--ansi")
        .arg("--delimiter=\t")
        .arg("--with-nth=1..")
        .arg(format!("--preview={preview_cmd}"))
        .arg("--preview-window=right:50%:wrap")
        .args(keybindings(args.resource))
        .spawn()
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                "fzf not found. Install fzf to use --fzf.".to_string()
            } else {
                format!("failed to spawn fzf: {e}")
            }
        })?;

    if let Some(mut stdin) = fzf.stdin.take() {
        stdin.write_all(input.as_bytes()).ok();
    }

    let status = fzf.wait().map_err(|e| format!("fzf error: {e}"))?;
    if !status.success() {
        // User cancelled — not an error
    }
    Ok(())
}

fn keybindings(resource: super::resource::ResourceKind) -> Vec<String> {
    use super::resource::ResourceKind;
    let mut binds = vec![
        "--bind=ctrl-y:execute-silent(echo -n {1} | xclip -selection clipboard 2>/dev/null || echo -n {1} | pbcopy 2>/dev/null)".to_string(),
        "--bind=ctrl-e:execute($EDITOR {2} 2>/dev/null)".to_string(),
    ];
    match resource {
        ResourceKind::Tests => {
            // become() replaces fzf with the command — no flicker, clean handoff
            // {1} = node_id (e.g. tests/test_a.py::test_foo)
            binds.push(r#"--bind=enter:become(oxitest run -E "name(={1})")"#.to_string());
            binds.push(r#"--bind=ctrl-r:become(oxitest debug -E "name(={1})")"#.to_string());
        }
        ResourceKind::Fixtures => {
            binds.push("--bind=enter:execute(oxitest query fixtures --tree)".to_string());
        }
        _ => {}
    }
    binds
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keybindings_tests_has_enter_and_debug() {
        let binds = keybindings(super::super::resource::ResourceKind::Tests);
        assert!(binds.iter().any(|b| b.contains("enter")));
        assert!(binds.iter().any(|b| b.contains("ctrl-r")));
    }

    #[test]
    fn keybindings_always_has_copy() {
        let binds = keybindings(super::super::resource::ResourceKind::Marks);
        assert!(binds.iter().any(|b| b.contains("ctrl-y")));
    }

    #[test]
    fn keybindings_fixtures_has_tree() {
        let binds = keybindings(super::super::resource::ResourceKind::Fixtures);
        assert!(binds.iter().any(|b| b.contains("--tree")));
    }
}
