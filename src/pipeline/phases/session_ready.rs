use pyo3::prelude::*;

use super::super::{collection, helpers, Collected, Pipeline, SessionReady};
use crate::cache::ModuleCache;
use crate::types::ExitCode;
use crate::{bridge, collector, config, filter, query};

impl Pipeline<SessionReady> {
    pub(crate) fn collect(self, py: Python<'_>) -> Result<Pipeline<Collected>, ExitCode> {
        let (
            mut shared,
            SessionReady {
                test_files,
                conftest_files,
                session,
                session_violations,
            },
        ) = self.into_parts();
        shared.cache.invalidate_modules();
        let (mut items, errors, raw_violations, profile) =
            collection::collect_items(py, &test_files, &shared.cfg, &session, &mut shared.cache);

        // Collect doctest items if --doctest-modules is enabled.
        if shared.cfg.doctest_modules {
            let doctest_files = collector::collect_doctest_files(&shared.cfg);
            let doctest_items = collection::collect_doctest_items(&doctest_files);
            tracing::info!(
                doctest_files = doctest_files.len(),
                doctest_items = doctest_items.len(),
                "collected doctest items"
            );
            items.extend(doctest_items);
        }

        if !errors.is_empty() {
            return Err(helpers::early_exit_with_error(&errors, &|| {
                shared.make_error_reporter()
            }));
        }

        // Merge session-phase violations with collection violations.
        let mut merged_violations = session_violations;
        merged_violations.extend(raw_violations);

        // Detect unused fixtures when strict mode is enabled.
        if shared.cfg.strict.is_some() && !shared.cfg.has_explicit_paths {
            if let Ok(unused) = bridge::find_unused_fixtures(py, &session, &items) {
                merged_violations.extend(unused);
            }
        }

        // Apply node ID filter early.
        let items = filter::filter_by_node_ids(
            items,
            &shared.cfg.node_ids,
            &shared.cfg.node_id_source_files,
        );

        if let Some(ref prof) = profile {
            eprintln!("{}", collection::format_collection_profile(prof));
        }

        Ok(shared.into_pipeline(Collected {
            test_files,
            conftest_files,
            session,
            items,
            raw_violations: merged_violations,
            collection_profile: profile,
        }))
    }

    pub(crate) fn query(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query only called for Query command");
        };

        if args.tree {
            if args.resource != query::resource::ResourceKind::Fixtures {
                eprintln!("error: --tree is only valid for the 'fixtures' resource");
                return Ok(ExitCode::UsageError);
            }
            let verbosity = self.cfg.verbosity as i32;
            match query::bridge::tree_fixtures(
                &self.state.session,
                py,
                verbosity,
                None,
                self.use_color,
            ) {
                Ok(output) => {
                    if output.starts_with("error:") {
                        eprintln!("{output}");
                        return Ok(ExitCode::Failure);
                    }
                    if !output.is_empty() {
                        println!("{output}");
                    }
                }
                Err(e) => {
                    eprintln!("Error rendering fixture tree: {e}");
                    return Ok(ExitCode::Failure);
                }
            }
            return Ok(ExitCode::Success);
        }

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.state.test_files,
                &self.state.conftest_files,
                Some(&self.state.session),
                &self.cfg,
                self.use_color,
            ) {
                Ok(()) => return Ok(ExitCode::Success),
                Err(e) => {
                    eprintln!("error: {e}");
                    return Ok(ExitCode::Failure);
                }
            }
        }

        match query::run_query(
            py,
            args,
            &self.state.test_files,
            &self.state.conftest_files,
            Some(&self.state.session),
            &self.cfg,
            self.is_tty,
            self.use_color,
        ) {
            Ok(output) => {
                if !output.is_empty() {
                    print!("{output}");
                }
                Ok(ExitCode::Success)
            }
            Err(msg) => {
                eprintln!("error: {msg}");
                Ok(ExitCode::UsageError)
            }
        }
    }
}
