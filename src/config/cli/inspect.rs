use super::{FailedFilterArgs, FilteringArgs};

/// Arguments for `oxitest inspect`.
#[derive(clap::Args, Debug, Clone)]
pub struct InspectArgs {
    /// Jump directly to a node by name
    pub name: Option<String>,

    #[command(flatten)]
    pub filter: FilteringArgs,

    #[command(flatten)]
    pub failed_filter: FailedFilterArgs,
}

#[cfg(test)]
impl InspectArgs {
    #[expect(dead_code, reason = "test helper not yet exercised by any test")]
    pub fn default_for_test() -> Self {
        use clap::Parser;
        let parsed = super::OxitestCli::try_parse_from(["oxitest", "inspect"])
            .expect("default InspectArgs must parse");
        match parsed.command {
            Some(super::Command::Inspect(args)) => args,
            other => panic!("`oxitest inspect` must parse as Command::Inspect, got {other:?}"),
        }
    }
}
