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
        super::OxitestCli::try_parse_from(["oxitest", "inspect"])
            .expect("default InspectArgs must parse")
            .command
            .map(|cmd| match cmd {
                super::Command::Inspect(args) => args,
                _ => unreachable!(),
            })
            .unwrap()
    }
}
