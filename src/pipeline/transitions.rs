//! Pipeline phase transitions.
//!
//! Each transition method consumes `Pipeline` and produces
//! `Result<Pipeline, ExitCode>`. Each method guards on the expected
//! `PipelinePhase` variant with a `let ... else { unreachable!(...) }`.

use std::sync::Arc;

use pyo3::prelude::*;

use super::collection;
use super::execution::{self, ExecutionContext};
use super::helpers;
use super::{ExecutionResults, Pipeline, PipelinePhase, PipelineShared};
use crate::cache::{ModuleCache, OutcomeCache, TimingCache};
use crate::types::ExitCode;
use crate::{affected, bridge, collector, config, filter, parallel, query, reporter, types};

// ─── Helper functions (formerly in collected.rs) ─────────────────────────────

fn format_dsl_error(expression: &str, error: query::ast::DslError) -> String {
    use miette::{GraphicalReportHandler, GraphicalTheme};

    let report = miette::Report::new(error).with_source_code(expression.to_string());
    let mut buf = String::new();
    let handler = GraphicalReportHandler::new_themed(GraphicalTheme::unicode_nocolor());
    let _ = handler.render_report(&mut buf, report.as_ref());
    buf
}

fn dsl_error_exit(
    expression: &str,
    error: query::ast::DslError,
    shared: &PipelineShared,
) -> ExitCode {
    let msg = format_dsl_error(expression, error);
    shared
        .make_error_reporter()
        .finish(
            &[types::CollectError::PyError(msg)],
            false,
            &reporter::ReporterSession::new(0),
        )
        .code()
}

fn apply_query_dsl_filter(
    items: Vec<Arc<types::TestItem>>,
    expression: &str,
    shared: &PipelineShared,
) -> Result<Vec<Arc<types::TestItem>>, ExitCode> {
    let tokens = match query::compile::lex(expression) {
        Ok(t) => t,
        Err(e) => return Err(dsl_error_exit(expression, e, shared)),
    };
    let parsed = match query::compile::parse(tokens) {
        Ok(p) => p,
        Err(e) => return Err(dsl_error_exit(expression, e, shared)),
    };
    if let Err(e) = query::eval::validate_predicates(&parsed, &query::resource::ResourceKind::Tests)
    {
        return Err(dsl_error_exit(expression, e, shared));
    }
    Ok(items
        .into_iter()
        .filter(|item| {
            let entry = item_to_query_entry(item);
            query::eval::eval(&parsed, &entry)
        })
        .collect())
}

fn item_to_query_entry(item: &types::TestItem) -> query::resource::QueryEntry {
    let mut fields = std::collections::HashMap::new();
    fields.insert("name".to_string(), item.node_id.to_string());
    fields.insert("source".to_string(), item.module_path().to_string());
    fields.insert("mark".to_string(), item.markers.join(","));
    fields.insert("async".to_string(), item.is_async.to_string());
    query::resource::QueryEntry { fields }
}

/// Pre-computed filter state used by `file_passes_all_filters`.
struct FilterPredicates<'a> {
    node_ids: &'a [String],
    expression: Option<&'a str>,
    failed_ids: &'a std::collections::HashSet<String>,
    node_id_source_files: &'a std::collections::HashSet<camino::Utf8PathBuf>,
    has_node_ids: bool,
    has_failed_filter: bool,
}

// ─── Transition methods ──────────────────────────────────────────────────────

impl Pipeline {
    // 1. collect_files: Empty → FilesCollected
    pub(crate) fn collect_files(self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Empty = self.phase else {
            unreachable!("collect_files called outside Empty phase")
        };
        let (test_files, conftest_files) = collector::collect_files(&self.cfg).map_err(|e| {
            eprintln!("error: invalid glob pattern in python_files: {e}");
            ExitCode::UsageError
        })?;
        let (mut shared, _) = self.into_parts();
        shared.test_files = test_files;
        shared.conftest_files = conftest_files;
        Ok(Pipeline {
            shared,
            phase: PipelinePhase::FilesCollected,
        })
    }

    // 2. affected: FilesCollected → FilesCollected
    pub(crate) fn affected(mut self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("affected called outside FilesCollected phase")
        };
        if let Some(base_ref) = self.cfg.filter.affected.as_ref() {
            match affected::filter_affected_test_files(
                &self.shared.test_files,
                &self.cfg.rootdir,
                base_ref,
            ) {
                Ok(Some(files)) => {
                    if files.is_empty() {
                        println!("no changes detected — nothing to test");
                        return Err(ExitCode::Success);
                    }
                    tracing::info!(
                        affected = files.len(),
                        total = self.shared.test_files.len(),
                        base = base_ref.as_str(),
                        "running affected tests only"
                    );
                    self.shared.test_files = files;
                }
                Ok(None) => {
                    tracing::info!("pyproject.toml changed — running all tests");
                }
                Err(e) => {
                    tracing::warn!("--affected filtering failed ({e}), running all tests");
                }
            }
        }
        Ok(self)
    }

    // 3. prescan: FilesCollected → Prescanned
    pub(crate) fn prescan(self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("prescan called outside FilesCollected phase")
        };
        use rayon::prelude::*;

        // Phase 1: parallel AST parse — CPU-bound, no shared state.
        let file_results: Vec<_> = self
            .shared
            .test_files
            .par_iter()
            .map(|file| (file.clone(), crate::prescan::prescan_with_ast(file, false)))
            .collect();

        // Phase 2: sequential accumulation — cheap, preserves deterministic order.
        let mut prescan_data = Vec::with_capacity(file_results.len());
        let mut module_markers = std::collections::HashMap::new();
        let mut ast_weight_sum = crate::types::DurationMs::ZERO;

        for (file, result) in file_results {
            match result {
                crate::prescan::PrescanResult::HasTests(p) => {
                    for i in &p.items {
                        ast_weight_sum += i.body_weight;
                    }
                    if !p.module_markers.is_empty() {
                        module_markers.insert(file.clone(), p.module_markers);
                    }
                    prescan_data.push(crate::prescan::PrescanModule {
                        path: file,
                        items: p.items,
                        has_dynamic_collection: p.has_dynamic_collection,
                    });
                }
                crate::prescan::PrescanResult::NoTests => {
                    tracing::debug!(path = file.as_str(), "prescan: no tests, skipping");
                }
                crate::prescan::PrescanResult::Unavailable => {
                    prescan_data.push(crate::prescan::PrescanModule {
                        path: file,
                        items: vec![],
                        has_dynamic_collection: true,
                    });
                }
            }
        }

        let (mut shared, _) = self.into_parts();

        shared.ast_weight = if ast_weight_sum > crate::types::DurationMs::ZERO {
            Some(ast_weight_sum)
        } else {
            None
        };

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Prescanned {
                prescan_data,
                module_markers,
            },
        })
    }

    // 4. filter_metadata: Prescanned → MetadataFiltered
    pub(crate) fn filter_metadata(self) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Prescanned {
            ref prescan_data,
            ref module_markers,
        } = self.phase
        else {
            unreachable!("filter_metadata called outside Prescanned phase")
        };

        let has_node_ids = !self.cfg.filter.node_ids.is_empty();
        let has_expression = match &self.command {
            config::Command::Run(a) => a.filter.expression.is_some(),
            config::Command::Debug(a) => a.filter.expression.is_some(),
            _ => false,
        };
        let has_failed_filter = matches!(self.cfg.filter.failed, Some(config::FailedMode::Only));
        let has_affected = self.cfg.filter.affected.is_some();
        let is_filtered = has_node_ids || has_expression || has_failed_filter || has_affected;

        if !is_filtered {
            let all_modules: Vec<camino::Utf8PathBuf> =
                prescan_data.iter().map(|m| m.path.clone()).collect();
            let (shared, _) = self.into_parts();
            return Ok(Pipeline {
                shared,
                phase: PipelinePhase::MetadataFiltered {
                    modules_to_import: all_modules,
                },
            });
        }

        // Prepare filter inputs once before the loop.
        let expression = match &self.command {
            config::Command::Run(a) => a.filter.expression.clone(),
            config::Command::Debug(a) => a.filter.expression.clone(),
            _ => None,
        };
        let failed_ids = if has_failed_filter {
            self.cache.last_failed_ids()
        } else {
            std::collections::HashSet::new()
        };
        let node_ids: Vec<String> = self
            .cfg
            .filter
            .node_ids
            .iter()
            .map(|n| n.to_string())
            .collect();
        let source_files = self.cfg.filter.source_files();

        let preds = FilterPredicates {
            node_ids: &node_ids,
            expression: expression.as_deref(),
            failed_ids: &failed_ids,
            node_id_source_files: &source_files,
            has_node_ids,
            has_failed_filter,
        };

        use rayon::prelude::*;

        let modules_to_import: Vec<camino::Utf8PathBuf> = prescan_data
            .par_iter()
            .filter_map(|m| {
                if m.has_dynamic_collection
                    || file_passes_all_filters(&m.path, &m.items, &preds, module_markers)
                {
                    Some(m.path.clone())
                } else {
                    None
                }
            })
            .collect();

        let filtered_conftests =
            collector::conftests_for_modules(&self.shared.conftest_files, &modules_to_import);

        tracing::info!(
            total_files = prescan_data.len(),
            matched_files = modules_to_import.len(),
            conftests = filtered_conftests.len(),
            "lazy collection: filtered by prescan metadata"
        );

        let (mut shared, _) = self.into_parts();
        shared.conftest_files = filtered_conftests;
        Ok(Pipeline {
            shared,
            phase: PipelinePhase::MetadataFiltered { modules_to_import },
        })
    }

    // 5a. session from FilesCollected: FilesCollected → SessionReady
    // 5b. session from MetadataFiltered: MetadataFiltered → SessionReady
    pub(crate) fn session(self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        match self.phase {
            PipelinePhase::FilesCollected => {
                let (session, fixture_violations) =
                    helpers::init_session(py, &self.shared.conftest_files, &self.cfg, || {
                        self.make_error_reporter()
                    })?;
                let (shared, _) = self.into_parts();
                Ok(Pipeline {
                    shared,
                    phase: PipelinePhase::SessionReady {
                        session,
                        session_violations: fixture_violations,
                    },
                })
            }
            PipelinePhase::MetadataFiltered { .. } => {
                let (session, fixture_violations) =
                    helpers::init_session(py, &self.shared.conftest_files, &self.cfg, || {
                        self.make_error_reporter()
                    })?;
                let (mut shared, phase) = self.into_parts();
                let PipelinePhase::MetadataFiltered { modules_to_import } = phase else {
                    unreachable!("phase was already matched as MetadataFiltered")
                };
                // Replace test_files with the filtered modules to import.
                shared.test_files = modules_to_import;
                Ok(Pipeline {
                    shared,
                    phase: PipelinePhase::SessionReady {
                        session,
                        session_violations: fixture_violations,
                    },
                })
            }
            _ => unreachable!("session called outside FilesCollected or MetadataFiltered phase"),
        }
    }

    // 6. collect: SessionReady → Collected
    pub(crate) fn collect(self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::SessionReady {
            session,
            session_violations,
        } = self.phase
        else {
            unreachable!("collect called outside SessionReady phase")
        };
        let mut shared = self.shared;
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

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Collected {
                session,
                items,
                raw_violations: merged_violations,
            },
        })
    }

    // 7. query_without_session: FilesCollected → terminal
    pub(crate) fn query_without_session(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::FilesCollected = &self.phase else {
            unreachable!("query_without_session called outside FilesCollected phase")
        };
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query_without_session only called for Query command");
        };

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.shared.test_files,
                &self.shared.conftest_files,
                None,
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
            None,
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

    // 8. query: SessionReady → terminal
    pub(crate) fn query(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::SessionReady { ref session, .. } = self.phase else {
            unreachable!("query called outside SessionReady phase")
        };
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query only called for Query command");
        };

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

    // 9. validate: Collected → Collected
    pub(crate) fn validate(self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Collected {
            ref session,
            ref items,
            ..
        } = self.phase
        else {
            unreachable!("validate called outside Collected phase")
        };

        let errors = bridge::validate_fixture_names(py, session, items)
            .map_err(|_| ExitCode::CollectError)?;

        if errors.is_empty() {
            return Ok(self);
        }

        let registered = bridge::registered_fixture_names(py, session).unwrap_or_default();
        let full_message = super::format_fixture_errors(&errors, &registered);
        let err = types::CollectError::PyError(full_message);
        Err(helpers::early_exit_with_error(&[err], &|| {
            self.make_error_reporter()
        }))
    }

    // 10. strict_or_skip: Collected → Ready
    pub(crate) fn strict_or_skip(self, _py: Python<'_>) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Collected {
            session,
            items,
            raw_violations,
        } = self.phase
        else {
            unreachable!("strict_or_skip called outside Collected phase")
        };
        let shared = self.shared;

        let result =
            helpers::apply_strict_mode(&shared.cfg, items, raw_violations, shared.use_color)?;

        // ── Item-level filtering (formerly PreFilter::filter) ────────────

        // Node ID filter (positional node IDs).
        let source_files = shared.cfg.filter.source_files();
        let items = filter::filter_by_node_ids(
            result.clean_items,
            &shared.cfg.filter.node_ids,
            &source_files,
        );

        let expression = match &shared.command {
            config::Command::Run(a) => a.filter.expression.clone(),
            config::Command::Debug(a) => a.filter.expression.clone(),
            _ => None,
        };

        // Query DSL filter (-E).
        let items = if let Some(expr_str) = expression.as_deref() {
            apply_query_dsl_filter(items, expr_str, &shared)?
        } else {
            items
        };

        // Last-failed filter (--failed=only / --failed=first).
        let total_before_failed_filter = items.len();
        let items = match shared.cfg.filter.failed {
            Some(config::FailedMode::Only) => {
                let failed_ids = shared.cache.last_failed_ids();
                if failed_ids.is_empty() {
                    tracing::info!(
                        count = items.len(),
                        "no recorded failures — running all tests"
                    );
                    items
                } else {
                    let filtered = filter::filter_last_failed(items, &failed_ids);
                    tracing::info!(
                        running = filtered.len(),
                        total = total_before_failed_filter,
                        "running tests in --failed=only mode"
                    );
                    filtered
                }
            }
            Some(config::FailedMode::First) => {
                let failed_ids = shared.cache.last_failed_ids();
                filter::sort_failed_first(items, &failed_ids)
            }
            None => items,
        };

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Ready {
                session,
                clean_items: items,
                violated_items: result.violated_items,
                all_violations: result.all_violations,
                suite_lines: result.suite_lines,
            },
        })
    }

    // 11. execute: Ready → Executed
    pub(crate) fn execute(self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        let PipelinePhase::Ready {
            session,
            clean_items,
            violated_items,
            all_violations,
            suite_lines,
        } = self.phase
        else {
            unreachable!("execute called outside Ready phase")
        };
        let mut shared = self.shared;

        let total = violated_items.len() + clean_items.len();
        let fn_count = {
            let mut seen = std::collections::HashSet::new();
            for item in clean_items.iter().chain(violated_items.iter()) {
                seen.insert((item.module_path(), &item.fn_name));
            }
            seen.len()
        };
        let async_count = clean_items.iter().filter(|i| i.is_async).count();
        let max_name_width = clean_items
            .iter()
            .chain(violated_items.iter())
            .map(|i| i.fn_name.len())
            .max()
            .unwrap_or(30);
        shared.cache.invalidate(&clean_items);

        // Fetch plugin reporters from Python registry.
        let plugin_reporters: Vec<Box<dyn reporter::Reporter>> =
            if !shared.cfg.features.plugins.is_empty() {
                bridge::get_plugin_reporters(py, &session)
                    .unwrap_or_default()
                    .into_iter()
                    .map(|obj| {
                        Box::new(reporter::plugin::PyPluginReporter::new(obj))
                            as Box<dyn reporter::Reporter>
                    })
                    .collect()
            } else {
                vec![]
            };

        let (json_path, junit_path) = match &shared.command {
            crate::config::Command::Run(a) => (a.json.clone(), a.junit_xml.clone()),
            _ => (None, None),
        };

        let mut rep = reporter::make_reporter(
            shared
                .base
                .clone()
                .total(total)
                .fn_count(fn_count)
                .async_count(async_count)
                .name_width(max_name_width)
                .strict_suite_lines(suite_lines)
                .build(),
            shared.is_tty,
            json_path,
            junit_path,
            plugin_reporters,
        );

        let exec_ctx = ExecutionContext {
            cfg: &shared.cfg,
            cache: &shared.cache,
            session: &session,
            conftest_files: &shared.conftest_files,
            python_bin: &shared.python_bin,
            ast_weight: shared.ast_weight,
        };

        let parallel::PhaseResult {
            interrupted,
            timings,
        } = execution::execute(
            py,
            &clean_items,
            violated_items,
            all_violations,
            &exec_ctx,
            rep.as_mut(),
        );

        Ok(Pipeline {
            shared,
            phase: PipelinePhase::Executed {
                session,
                items: clean_items,
                execution_results: ExecutionResults {
                    timings,
                    interrupted,
                    reporter: rep,
                },
            },
        })
    }

    // 12. retry: Executed → Executed
    pub(crate) fn retry(mut self, py: Python<'_>) -> Result<Pipeline, ExitCode> {
        // Extract config values before borrowing self.phase mutably.
        let max_retries = self.cfg.exec.retries;
        let delay_secs = self.cfg.exec.retries_delay_secs;
        let timeout_secs = self.cfg.exec.timeout_secs;
        let keep_tmp_str = self.cfg.output.keep_tmp;
        let show_locals = self.cfg.output.show_locals;
        let show_internals = self.cfg.output.show_internals;

        let PipelinePhase::Executed {
            ref session,
            ref items,
            ref mut execution_results,
        } = self.phase
        else {
            unreachable!("retry called outside Executed phase")
        };

        let not_interrupted = !execution_results.interrupted;
        if max_retries == 0 || !not_interrupted {
            return Ok(self);
        }

        let failed_items = crate::retry::identify_failed_items(items, &execution_results.timings);
        if failed_items.is_empty() {
            return Ok(self);
        }

        let retry_ctx = crate::retry::RetryContext {
            py,
            max_retries,
            delay_secs,
            session,
            timeout_secs,
            opts: execution::DebugOptions {
                debug_mode: None,
                keep_tmp: keep_tmp_str.as_ref().map(|m| m.as_str()),
                show_locals,
                show_internals,
            },
        };
        let crate::retry::RetryResult {
            flaky_ids,
            retry_timings,
        } = crate::retry::run_retries(
            &retry_ctx,
            &failed_items,
            execution_results.reporter.as_mut(),
        );

        let original_timings = std::mem::take(&mut execution_results.timings);
        execution_results.timings =
            crate::retry::merge_flaky_timings(original_timings, &flaky_ids, retry_timings);

        Ok(self)
    }

    // 13. finalize: Executed → terminal
    pub(crate) fn finalize(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let PipelinePhase::Executed {
            session,
            items: _,
            execution_results,
        } = self.phase
        else {
            unreachable!("finalize called outside Executed phase")
        };
        let mut shared = self.shared;
        let ExecutionResults {
            timings,
            interrupted,
            mut reporter,
        } = execution_results;
        if let Ok(ft) = reporter::bridge::get_fixture_timings(&session, py)
            && !ft.is_empty()
        {
            reporter.set_fixture_timings(ft);
        }

        helpers::finalize(
            &mut shared.cache,
            &timings,
            shared.cfg.features.cache_max_age,
            &shared.rootdir,
        );

        if let Ok(stats) = reporter::bridge::get_cache_stats(&session, py)
            && stats.hits + stats.misses > 0
        {
            reporter.set_fixture_cache_stats(stats.hits, stats.misses, stats.breakdown);
        }

        let code = reporter
            .finish(&[], interrupted, &reporter::ReporterSession::new(0))
            .code();
        Ok(code)
    }
}

// ─── Filter helper (formerly on Pipeline<Prescanned>) ────────────────────────

/// Returns true if the file should be imported given the active filters.
///
/// Uses early-return false for each failing filter (AND semantics, short-circuit).
fn file_passes_all_filters(
    path: &camino::Utf8Path,
    items: &[crate::prescan::PrescanItem],
    preds: &FilterPredicates<'_>,
    module_markers: &std::collections::HashMap<camino::Utf8PathBuf, Vec<String>>,
) -> bool {
    if preds.has_node_ids {
        // Only apply node ID filtering to files that came from node ID args,
        // not bare path args. Files from bare paths pass through unconditionally.
        let is_node_id_source =
            preds.node_id_source_files.is_empty() || preds.node_id_source_files.contains(path);
        if is_node_id_source && !filter::file_matches_node_ids(items, path.as_str(), preds.node_ids)
        {
            return false;
        }
    }

    if let Some(expr) = preds.expression {
        let module_marks = module_markers.get(path).map(|v| v.as_slice());
        if !filter::file_matches_expression(items, path.as_str(), expr, module_marks) {
            return false;
        }
    }

    if preds.has_failed_filter
        && !preds.failed_ids.is_empty()
        && !filter::file_matches_last_failed(items, path.as_str(), preds.failed_ids)
    {
        return false;
    }

    true
}
