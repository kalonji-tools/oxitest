//! Doctest scanning, subject enumeration, and coverage checking.
//!
//! `scanner` walks module docstrings for `>>>` blocks. `subjects`, `alias`,
//! and `coverage` implement the coverage rule (wayfinder #1602).

mod scanner;

pub use scanner::scan_doctests;

pub mod alias;
pub mod coverage;
pub mod subjects;
