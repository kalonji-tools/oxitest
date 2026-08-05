mod diagnostic;
mod diff;
pub mod suggestions;
mod summary;

pub use diagnostic::{case_sep, fmt_diagnostic_block, pad_to, sep_width};
pub use diff::fmt_diff;
pub use summary::{fmt_diagnostics_block, fmt_summary, fmt_tip_block, plural};
