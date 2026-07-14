mod diagnostic;
mod diff;
pub(crate) mod suggestions;
mod summary;

pub(crate) use diagnostic::{case_sep, fmt_diagnostic_block, pad_to, sep_width};
pub(crate) use diff::fmt_diff;
pub(crate) use summary::{fmt_diagnostics_block, fmt_summary, fmt_tip_block, plural};
