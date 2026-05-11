mod diagnostic;
mod summary;

pub(crate) use diagnostic::{case_sep, fmt_diagnostic_block, pad_to, sep_width};
pub(crate) use summary::{fmt_summary, fmt_tip_block, fmt_warning_block};
