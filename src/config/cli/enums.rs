// ── DebugMode ────────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum DebugMode {
    /// Drop into pdb on first test failure
    PostMortem,
    /// Drop into pdb before every test
    Always,
}

// ── CovReportFormat ──────────────────────────────────────────────────────────

/// Coverage report format.
#[derive(Clone, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum CovReportFormat {
    /// Print coverage table to terminal
    Term,
    /// Generate HTML report (htmlcov/)
    Html,
    /// Generate Cobertura XML (coverage.xml)
    Xml,
    /// Generate JSON report (coverage.json)
    Json,
    /// No report output (just collect .coverage data)
    None,
}

impl CovReportFormat {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Term => "term",
            Self::Html => "html",
            Self::Xml => "xml",
            Self::Json => "json",
            Self::None => "none",
        }
    }
}
