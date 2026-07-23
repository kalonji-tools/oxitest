//! Doctest scanning, subject enumeration, and coverage checking.
//!
//! `scanner` walks module docstrings for `>>>` blocks (the pre-existing behaviour).
//! Later M1 tasks add `subjects`, `alias`, and `coverage` for the coverage rule
//! (wayfinder #1602).

mod scanner;

pub(crate) use scanner::scan_doctests;

pub(crate) mod alias;
pub(crate) mod coverage;
pub(crate) mod subjects;
