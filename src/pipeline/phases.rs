//! Pipeline phase transitions via typestate pattern.
//!
//! Each transition method consumes `Pipeline<S>` and produces
//! `Result<Pipeline<NextState>, ExitCode>`. Illegal ordering is a compile error.

use pyo3::prelude::*;

use super::{
    collection, execution, helpers, Collected, Empty, Executed, ExecutionResults, FilesCollected,
    MetadataFiltered, Pipeline, PreFilter, Prescanned, Ready, SessionReady,
};
use crate::cache::{ModuleCache, OutcomeCache, TimingCache};
use crate::types::ExitCode;
use crate::{
    affected, bridge, collector, config, filter, parallel, query, reporter, retry, strict, types,
};

// ─── Pipeline<Empty> ─────────────────────────────────────────────────────────

impl Pipeline<Empty> {
    pub(crate) fn collect_files(self) -> Result<Pipeline<FilesCollected>, ExitCode> {
        let (test_files, conftest_files) = collector::collect_files(&self.cfg).map_err(|e| {
            eprintln!("error: invalid glob pattern in python_files: {e}");
            ExitCode::UsageError
        })?;
        let (shared, _) = self.into_parts();
        Ok(shared.into_pipeline(FilesCollected {
            test_files,
            conftest_files,
        }))
    }
}

// ─── Pipeline<FilesCollected> ────────────────────────────────────────────────

impl Pipeline<FilesCollected> {
    pub(crate) fn affected(mut self) -> Result<Pipeline<FilesCollected>, ExitCode> {
        if let Some(base_ref) = self.cfg.affected.as_ref() {
            match affected::filter_affected_test_files(
                &self.state.test_files,
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
                        total = self.state.test_files.len(),
                        base = base_ref.as_str(),
                        "running affected tests only"
                    );
                    self.state.test_files = files;
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

    pub(crate) fn prescan(self) -> Result<Pipeline<Prescanned>, ExitCode> {
        let mut prescan_data = Vec::with_capacity(self.state.test_files.len());
        let mut module_markers = std::collections::HashMap::new();
        let mut ast_weight_sum = 0.0f64;

        for file in &self.state.test_files {
            let result = crate::prescan::prescan_with_ast(file, false);
            match result {
                crate::prescan::PrescanResult::HasTests {
                    items,
                    has_dynamic_collection,
                    module_markers: file_marks,
                    ..
                } => {
                    ast_weight_sum += items.iter().map(|i| i.body_weight_ms).sum::<f64>();
                    prescan_data.push((file.clone(), items, has_dynamic_collection));
                    if !file_marks.is_empty() {
                        module_markers.insert(file.clone(), file_marks);
                    }
                }
                crate::prescan::PrescanResult::NoTests => {
                    tracing::debug!(path = file.as_str(), "prescan: no tests, skipping");
                }
                crate::prescan::PrescanResult::Unavailable => {
                    prescan_data.push((file.clone(), vec![], true));
                }
            }
        }

        let (
            mut shared,
            FilesCollected {
                test_files,
                conftest_files,
            },
        ) = self.into_parts();

        shared.ast_weight_ms = if ast_weight_sum > 0.0 {
            Some(ast_weight_sum)
        } else {
            None
        };

        Ok(shared.into_pipeline(Prescanned {
            test_files,
            conftest_files,
            prescan_data,
            module_markers,
        }))
    }

    pub(crate) fn session(self, py: Python<'_>) -> Result<Pipeline<SessionReady>, ExitCode> {
        let (session, fixture_violations) =
            match bridge::FixtureSession::new(py, &self.state.conftest_files) {
                Ok(pair) => pair,
                Err(e) => {
                    let err = types::CollectError::PyError(format!(
                        "Failed to load conftest fixtures: {}",
                        e
                    ));
                    return Err(helpers::early_exit_with_error(&[err], &|| {
                        self.make_error_reporter()
                    }));
                }
            };

        if !self.cfg.plugins.is_empty() {
            if let Err(e) = session.load_plugins(py, &self.cfg.plugins, &self.cfg.plugin_settings) {
                let err = types::CollectError::PyError(format!("Plugin loading failed: {}", e));
                return Err(helpers::early_exit_with_error(&[err], &|| {
                    self.make_error_reporter()
                }));
            }
        }

        if let Err(e) = session.init_async_backend(py, &self.cfg.async_backend) {
            let err = types::CollectError::PyError(format!("Async backend init failed: {}", e));
            return Err(helpers::early_exit_with_error(&[err], &|| {
                self.make_error_reporter()
            }));
        }

        let (
            shared,
            FilesCollected {
                test_files,
                conftest_files,
            },
        ) = self.into_parts();
        Ok(shared.into_pipeline(SessionReady {
            test_files,
            conftest_files,
            session,
            session_violations: fixture_violations,
        }))
    }

    pub(crate) fn query_without_session(self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let config::Command::Query(ref args) = self.command else {
            unreachable!("query_without_session only called for Query command");
        };

        if args.fzf {
            match crate::query::fzf::run_fzf(
                py,
                args,
                &self.state.test_files,
                &self.state.conftest_files,
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
            &self.state.test_files,
            &self.state.conftest_files,
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
}

// ─── Pipeline<Prescanned> ───────────────────────────────────────────────────

impl Pipeline<Prescanned> {
    pub(crate) fn filter_metadata(self) -> Result<Pipeline<MetadataFiltered>, ExitCode> {
        let has_node_ids = !self.cfg.node_ids.is_empty();
        let has_expression = match &self.command {
            config::Command::Run(a) => a.filter.expression.is_some(),
            config::Command::Debug(a) => a.filter.expression.is_some(),
            _ => false,
        };
        let has_failed_filter = matches!(self.cfg.failed, Some(config::FailedMode::Only));
        let has_affected = self.cfg.affected.is_some();
        let is_filtered = has_node_ids || has_expression || has_failed_filter || has_affected;

        if !is_filtered {
            let all_modules: Vec<camino::Utf8PathBuf> = self
                .state
                .prescan_data
                .iter()
                .map(|(path, _, _)| path.clone())
                .collect();
            let (
                shared,
                Prescanned {
                    test_files,
                    conftest_files,
                    ..
                },
            ) = self.into_parts();
            return Ok(shared.into_pipeline(MetadataFiltered {
                test_files,
                conftest_files,
                modules_to_import: all_modules,
                is_filtered: false,
            }));
        }

        let mut modules_to_import = Vec::new();
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
        let node_ids: Vec<String> = self.cfg.node_ids.iter().map(|n| n.to_string()).collect();

        for (path, items, has_dynamic) in &self.state.prescan_data {
            if *has_dynamic {
                modules_to_import.push(path.clone());
                continue;
            }

            // For each active filter type, check if any item in the file matches.
            // All active filters must have at least one match for the file to be included.
            let mut dominated = true;

            if has_node_ids {
                // Only apply node ID filtering to files that came from node ID args,
                // not bare path args. Files from bare paths pass through unconditionally.
                let is_node_id_source = self.cfg.node_id_source_files.is_empty()
                    || self.cfg.node_id_source_files.contains(path);
                if is_node_id_source {
                    let matched =
                        filter::filter_prescan_by_node_ids(items, path.as_str(), &node_ids);
                    if matched.is_empty() {
                        dominated = false;
                    }
                }
            }

            if dominated {
                if let Some(ref expr) = expression {
                    // If the file has module-level marks, augment items with them
                    // so expression filtering sees module marks on every item.
                    let file_marks = self.state.module_markers.get(path);
                    if let Some(marks) = file_marks {
                        let augmented: Vec<crate::prescan::PrescanItem> = items
                            .iter()
                            .map(|item| {
                                let mut aug = item.clone();
                                for m in marks {
                                    if !aug.markers.iter().any(|em| em.name == *m) {
                                        aug.markers.push(crate::prescan::PrescanMarker {
                                            name: m.clone(),
                                            has_dynamic_args: false,
                                        });
                                    }
                                }
                                aug
                            })
                            .collect();
                        let matched =
                            filter::filter_prescan_by_expression(&augmented, path.as_str(), expr);
                        if matched.is_empty() {
                            dominated = false;
                        }
                    } else {
                        let matched =
                            filter::filter_prescan_by_expression(items, path.as_str(), expr);
                        if matched.is_empty() {
                            dominated = false;
                        }
                    }
                }
            }

            if dominated && has_failed_filter && !failed_ids.is_empty() {
                let matched = filter::filter_prescan_last_failed(items, path.as_str(), &failed_ids);
                if matched.is_empty() {
                    dominated = false;
                }
            }

            if dominated {
                modules_to_import.push(path.clone());
            }
        }

        let filtered_conftests =
            collector::conftests_for_modules(&self.state.conftest_files, &modules_to_import);

        tracing::info!(
            total_files = self.state.prescan_data.len(),
            matched_files = modules_to_import.len(),
            conftests = filtered_conftests.len(),
            "lazy collection: filtered by prescan metadata"
        );

        let (shared, Prescanned { test_files, .. }) = self.into_parts();
        Ok(shared.into_pipeline(MetadataFiltered {
            test_files,
            conftest_files: filtered_conftests,
            modules_to_import,
            is_filtered: true,
        }))
    }
}

// ─── Pipeline<MetadataFiltered> ─────────────────────────────────────────────

impl Pipeline<MetadataFiltered> {
    pub(crate) fn session(self, py: Python<'_>) -> Result<Pipeline<SessionReady>, ExitCode> {
        let (session, fixture_violations) =
            match bridge::FixtureSession::new(py, &self.state.conftest_files) {
                Ok(pair) => pair,
                Err(e) => {
                    let err = types::CollectError::PyError(format!(
                        "Failed to load conftest fixtures: {}",
                        e
                    ));
                    return Err(helpers::early_exit_with_error(&[err], &|| {
                        self.make_error_reporter()
                    }));
                }
            };

        if !self.cfg.plugins.is_empty() {
            if let Err(e) = session.load_plugins(py, &self.cfg.plugins, &self.cfg.plugin_settings) {
                let err = types::CollectError::PyError(format!("Plugin loading failed: {}", e));
                return Err(helpers::early_exit_with_error(&[err], &|| {
                    self.make_error_reporter()
                }));
            }
        }

        if let Err(e) = session.init_async_backend(py, &self.cfg.async_backend) {
            let err = types::CollectError::PyError(format!("Async backend init failed: {}", e));
            return Err(helpers::early_exit_with_error(&[err], &|| {
                self.make_error_reporter()
            }));
        }

        let (
            shared,
            MetadataFiltered {
                test_files: _,
                conftest_files,
                modules_to_import,
                is_filtered: _,
            },
        ) = self.into_parts();
        Ok(shared.into_pipeline(SessionReady {
            test_files: modules_to_import,
            conftest_files,
            session,
            session_violations: fixture_violations,
        }))
    }
}

// ─── Pipeline<SessionReady> ─────────────────────────────────────────────────

impl Pipeline<SessionReady> {
    pub(crate) fn collect(mut self, py: Python<'_>) -> Result<Pipeline<Collected>, ExitCode> {
        self.cache.invalidate_modules();
        let (mut items, errors, raw_violations, profile) = collection::collect_items(
            py,
            &self.state.test_files,
            &self.cfg,
            &self.state.session,
            &mut self.cache,
        );

        // Collect doctest items if --doctest-modules is enabled.
        if self.cfg.doctest_modules {
            let doctest_files = collector::collect_doctest_files(&self.cfg);
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
                self.make_error_reporter()
            }));
        }

        let (
            shared,
            SessionReady {
                test_files,
                conftest_files,
                session,
                session_violations,
            },
        ) = self.into_parts();

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

// ─── Pipeline<Collected> ─────────────────────────────────────────────────────

impl Pipeline<Collected> {
    pub(crate) fn validate(self, py: Python<'_>) -> Result<Pipeline<Collected>, ExitCode> {
        let errors = bridge::validate_fixture_names(py, &self.state.session, &self.state.items)
            .map_err(|_| ExitCode::CollectError)?;

        if errors.is_empty() {
            return Ok(self);
        }

        let registered =
            bridge::registered_fixture_names(py, &self.state.session).unwrap_or_default();
        let full_message = super::format_fixture_errors(&errors, &registered);
        let err = types::CollectError::PyError(full_message);
        Err(helpers::early_exit_with_error(&[err], &|| {
            self.make_error_reporter()
        }))
    }

    pub(crate) fn strict_or_skip(self, _py: Python<'_>) -> Result<Pipeline<PreFilter>, ExitCode> {
        let (
            shared,
            Collected {
                test_files,
                conftest_files,
                session,
                items,
                raw_violations,
                collection_profile: _,
            },
        ) = self.into_parts();

        if shared.cfg.strict.is_none() {
            return Ok(shared.into_pipeline(PreFilter {
                test_files,
                conftest_files,
                session,
                clean_items: items,
                violated_items: vec![],
                all_violations: vec![],
                suite_lines: vec![],
            }));
        }

        // Build the full violation list.
        let mut all_violations = strict::check_config(&shared.cfg);
        all_violations.extend(strict::check_collected(raw_violations));

        // Abort mode: print and signal early exit.
        if shared.cfg.strict == Some(config::StrictMode::Abort) && !all_violations.is_empty() {
            let abort_lines: Vec<String> = all_violations
                .iter()
                .map(strict::format_violation_line)
                .collect();
            reporter::print_strict_abort(&abort_lines, shared.use_color);
            return Err(ExitCode::CollectError);
        }

        // Enforce mode: build suite-level violation lines.
        let suite_lines: Vec<String> = if shared.cfg.strict == Some(config::StrictMode::Enforce) {
            strict::suite_level(&all_violations)
                .iter()
                .map(|v| v.to_string())
                .collect()
        } else {
            vec![]
        };

        // Partition items into violated vs. clean.
        let (violated_items, clean_items): (Vec<_>, Vec<_>) =
            if shared.cfg.strict == Some(config::StrictMode::Enforce) {
                let violated_ids: std::collections::HashSet<&str> = all_violations
                    .iter()
                    .filter_map(|v| v.node_id())
                    .map(|id| id.as_ref())
                    .collect();
                items
                    .into_iter()
                    .partition(|i| violated_ids.contains(i.node_id.as_ref()))
            } else {
                (vec![], items)
            };

        Ok(shared.into_pipeline(PreFilter {
            test_files,
            conftest_files,
            session,
            clean_items,
            violated_items,
            all_violations,
            suite_lines,
        }))
    }
}

// ─── Pipeline<PreFilter> ─────────────────────────────────────────────────────

fn apply_query_dsl_filter(
    items: Vec<std::sync::Arc<types::TestItem>>,
    expression: &str,
    shared: &super::PipelineShared,
) -> Result<Vec<std::sync::Arc<types::TestItem>>, ExitCode> {
    let tokens = match query::compile::lex(expression) {
        Ok(t) => t,
        Err(e) => {
            return Err(shared
                .make_error_reporter()
                .finish(
                    &[types::CollectError::PyError(format!(
                        "invalid -E expression: {e}"
                    ))],
                    false,
                    &reporter::ReporterSession::new(0),
                )
                .code());
        }
    };
    let parsed = match query::compile::parse(tokens) {
        Ok(p) => p,
        Err(e) => {
            return Err(shared
                .make_error_reporter()
                .finish(
                    &[types::CollectError::PyError(format!(
                        "invalid -E expression: {e}"
                    ))],
                    false,
                    &reporter::ReporterSession::new(0),
                )
                .code());
        }
    };
    if let Err(e) = query::eval::validate_predicates(&parsed, &query::resource::ResourceKind::Tests)
    {
        return Err(shared
            .make_error_reporter()
            .finish(
                &[types::CollectError::PyError(format!(
                    "invalid -E expression: {e}"
                ))],
                false,
                &reporter::ReporterSession::new(0),
            )
            .code());
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
    fields.insert("source".to_string(), item.module_path.to_string());
    fields.insert("mark".to_string(), item.markers.join(","));
    fields.insert("async".to_string(), item.is_async.to_string());
    query::resource::QueryEntry { fields }
}

impl Pipeline<PreFilter> {
    pub(crate) fn filter(self, _py: Python<'_>) -> Result<Pipeline<Ready>, ExitCode> {
        let (
            shared,
            PreFilter {
                test_files,
                conftest_files,
                session,
                clean_items: items,
                violated_items,
                all_violations,
                suite_lines,
            },
        ) = self.into_parts();

        // Node ID filter (positional node IDs).
        let items = filter::filter_by_node_ids(
            items,
            &shared.cfg.node_ids,
            &shared.cfg.node_id_source_files,
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
        let items = match shared.cfg.failed {
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

        Ok(shared.into_pipeline(Ready {
            test_files,
            conftest_files,
            session,
            clean_items: items,
            violated_items,
            all_violations,
            suite_lines,
        }))
    }
}

// ─── Pipeline<Ready> ─────────────────────────────────────────────────────────

impl Pipeline<Ready> {
    pub(crate) fn execute(self, py: Python<'_>) -> Result<Pipeline<Executed>, ExitCode> {
        let (
            mut shared,
            Ready {
                test_files,
                conftest_files,
                session,
                clean_items,
                violated_items,
                all_violations,
                suite_lines,
            },
        ) = self.into_parts();

        let total = violated_items.len() + clean_items.len();
        let fn_count = {
            let mut seen = std::collections::HashSet::new();
            for item in clean_items.iter().chain(violated_items.iter()) {
                seen.insert((&item.module_path, &item.fn_name));
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
        let plugin_reporters: Vec<Box<dyn reporter::Reporter>> = if !shared.cfg.plugins.is_empty() {
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

        let exec_ctx = execution::ExecutionContext {
            cfg: &shared.cfg,
            cache: &shared.cache,
            session: &session,
            conftest_files: &conftest_files,
            python_bin: &shared.python_bin,
            ast_weight_ms: shared.ast_weight_ms,
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

        Ok(shared.into_pipeline(Executed {
            test_files,
            conftest_files,
            session,
            items: clean_items,
            execution_results: ExecutionResults {
                timings,
                interrupted,
                reporter: rep,
            },
        }))
    }
}

// ─── Pipeline<Executed> ──────────────────────────────────────────────────────

impl Pipeline<Executed> {
    pub(crate) fn retry(mut self, py: Python<'_>) -> Result<Pipeline<Executed>, ExitCode> {
        let not_interrupted = !self.state.execution_results.interrupted;
        if self.cfg.retries == 0 || !not_interrupted {
            return Ok(self);
        }

        let failed_items =
            retry::identify_failed_items(&self.state.items, &self.state.execution_results.timings);
        if failed_items.is_empty() {
            return Ok(self);
        }

        let retry_ctx = retry::RetryContext {
            py,
            max_retries: self.cfg.retries,
            delay_secs: self.cfg.retries_delay_secs,
            session: &self.state.session,
            timeout_secs: self.cfg.timeout_secs,
            keep_tmp: self.cfg.keep_tmp.as_ref().map(|m| m.as_str()),
            show_locals: self.cfg.show_locals,
            show_internals: self.cfg.show_internals,
        };
        let retry::RetryResult {
            flaky_ids,
            retry_timings,
        } = retry::run_retries(
            &retry_ctx,
            &failed_items,
            self.state.execution_results.reporter.as_mut(),
        );

        let original_timings = std::mem::take(&mut self.state.execution_results.timings);
        self.state.execution_results.timings =
            retry::merge_flaky_timings(original_timings, &flaky_ids, retry_timings);

        Ok(self)
    }

    pub(crate) fn finalize(mut self, py: Python<'_>) -> Result<ExitCode, ExitCode> {
        let ExecutionResults {
            timings,
            interrupted,
            mut reporter,
        } = self.state.execution_results;

        if let Ok(ft) = reporter::bridge::get_fixture_timings(&self.state.session, py) {
            if !ft.is_empty() {
                reporter.set_fixture_timings(ft);
            }
        }

        helpers::finalize(
            &mut self.cache,
            &timings,
            self.cfg.cache_max_age,
            &self.rootdir,
        );

        if let Ok(stats) = reporter::bridge::get_cache_stats(&self.state.session, py) {
            if stats.hits + stats.misses > 0 {
                reporter.set_fixture_cache_stats(stats.hits, stats.misses, stats.breakdown);
            }
        }

        let code = reporter
            .finish(&[], interrupted, &reporter::ReporterSession::new(0))
            .code();
        Ok(code)
    }
}
