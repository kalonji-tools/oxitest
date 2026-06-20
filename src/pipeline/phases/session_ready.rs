use pyo3::prelude::*;

use super::super::{Collected, Pipeline, SessionReady, collection, helpers};
use crate::cache::ModuleCache;
use crate::types::ExitCode;
use crate::{bridge, collector, config, filter, query};

impl Pipeline<SessionReady> {
    pub(crate) fn collect(self, py: Python<'_>) -> Result<Pipeline<Collected>, ExitCode> {
        let (
            mut shared,
            SessionReady {
                session,
                session_violations,
            },
        ) = self.into_parts();
        shared.cache.invalidate_modules();
        let (mut items, errors, raw_violations, profile) = collection::collect_items(
            py,
            &shared.test_files,
            &shared.cfg,
            &session,
            &mut shared.cache,
        );

        // Collect doctest items if --doctest-modules is enabled.
        if shared.cfg.paths.doctest_modules {
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
        if shared.cfg.markers.strict.is_some()
            && !shared.cfg.filter.has_explicit_paths
            && let Ok(unused) = bridge::find_unused_fixtures(py, &session, &items)
        {
            merged_violations.extend(unused);
        }

        // Apply node ID filter early.
        let source_files = shared.cfg.filter.source_files();
        let items = filter::filter_by_node_ids(items, &shared.cfg.filter.node_ids, &source_files);

        if let Some(ref prof) = profile {
            eprintln!("{}", collection::format_collection_profile(prof));
        }

        Ok(shared.into_pipeline(Collected {
            session,
            items,
            raw_violations: merged_violations,
        }))
    }

    pub(crate) fn query(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query only called for Query command");
        };

        let session = &self.state.session;

        if args.tree {
            if args.resource != query::resource::ResourceKind::Fixtures {
                eprintln!("error: --tree is only valid for the 'fixtures' resource");
                return Ok(ExitCode::UsageError);
            }
            let verbosity = self.cfg.output.verbosity as i32;
            match query::bridge::tree_fixtures(session, py, verbosity, None, self.use_color) {
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
                &self.shared.test_files,
                &self.shared.conftest_files,
                Some(session),
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
            &self.shared.test_files,
            &self.shared.conftest_files,
            Some(session),
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
