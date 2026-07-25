//! Doctest scanning, subject enumeration, and coverage checking.
//!
//! `scanner` walks module docstrings for `>>>` blocks. `subjects`, `alias`,
//! and `coverage` implement the coverage rule (wayfinder #1602).

mod scanner;

pub(crate) use scanner::scan_doctests;

pub(crate) mod alias;
pub(crate) mod coverage;
pub(crate) mod subjects;
