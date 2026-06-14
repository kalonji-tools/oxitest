//! Pipeline phase transitions via typestate pattern.
//!
//! Each transition method consumes `Pipeline<S>` and produces
//! `Result<Pipeline<NextState>, ExitCode>`. Illegal ordering is a compile error.

mod collected;
mod empty;
mod executed;
mod files_collected;
mod metadata_filtered;
mod prescanned;
mod ready;
mod session_ready;
