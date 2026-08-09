//! Target validation (#1797).
//!
//! A **Target** is a path, a directory or a node ID given as a command-line
//! argument. A Target that names something absent is a usage error, not an
//! empty run: exit 4, not exit 0.
//!
//! Path Targets are checked here, before `find_rootdir` runs, so a Target that
//! does not exist can never seed rootdir discovery. Literal node-ID Targets
//! cannot be checked this early — their module must be imported first — so
//! they are checked in [`crate::filter`] after collection.

use camino::Utf8PathBuf;

/// Path Targets that are not on the filesystem, in the order they were given.
///
/// A node ID is never passed here: `partition_positionals` has already
/// separated those, and a shell expands path globs before oxitest sees them.
#[must_use = "the caller decides the exit code from this list"]
pub fn missing_paths(paths: &[Utf8PathBuf]) -> Vec<Utf8PathBuf> {
    paths.iter().filter(|p| !p.exists()).cloned().collect()
}

/// Render the refusal for absent path Targets.
///
/// No `did you mean` suggestion. The mistake that motivated #1797 was
/// `test_fixtures.py` for `test_fixtures_dsl.py`, which is edit distance 4, so
/// the threshold used for fixture names would miss it and a wider threshold has
/// no evidence behind it.
#[must_use]
pub fn render_missing_paths(bad: &[Utf8PathBuf]) -> String {
    let mut out = String::from("error: no such path\n");
    for path in bad {
        out.push_str(&format!("  {path}\n"));
    }
    out.push_str(
        "\nA path that does not exist is a usage error, not an empty run.\n\
         Nothing ran. See docs/user/reference/exit-codes.md.\n",
    );
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_paths_reports_only_absent_targets() {
        let dir = assert_fs::TempDir::new().expect("temp dir for the fixture project");
        let real = dir.path().join("test_real.py");
        std::fs::write(&real, "def test_a(): pass\n").expect("write the present Target");
        let present = Utf8PathBuf::from_path_buf(real).expect("temp dir path is utf8");
        let absent = present
            .parent()
            .expect("the file has a parent directory")
            .join("test_absent.py");

        let bad = missing_paths(&[present, absent.clone()]);

        assert_eq!(
            bad,
            vec![absent],
            "only the absent Target may be reported — reporting a present one would refuse a valid run, and reporting neither is the #1797 defect"
        );
    }

    #[test]
    fn missing_paths_preserves_argument_order() {
        let first = Utf8PathBuf::from("zzz_absent_a.py");
        let second = Utf8PathBuf::from("aaa_absent_b.py");

        let bad = missing_paths(&[first.clone(), second.clone()]);

        assert_eq!(
            bad,
            vec![first, second],
            "the report follows the order the user typed, not sorted order — a user scanning the message matches it against their own command line"
        );
    }

    #[test]
    fn render_missing_paths_names_every_bad_target() {
        let rendered = render_missing_paths(&[
            Utf8PathBuf::from("missing_a.py"),
            Utf8PathBuf::from("missing_b.py"),
        ]);

        assert!(
            rendered.contains("missing_a.py") && rendered.contains("missing_b.py"),
            "both absent Targets must appear in the refusal — naming only the first makes the user re-run once per typo"
        );
    }
}
