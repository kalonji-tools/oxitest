//! Terminal color helper functions, shared across reporter and query subsystems.

macro_rules! color_fn {
    ($name:ident, |$s:ident| $styled:expr) => {
        pub(crate) fn $name(s: &str, use_color: bool) -> String {
            if use_color {
                let $s = console::style(s);
                $styled.to_string()
            } else {
                s.to_string()
            }
        }
    };
    // Second arm: for functions added before their call sites exist.
    (#[expect(dead_code)] $name:ident, |$s:ident| $styled:expr) => {
        #[expect(dead_code)]
        pub(crate) fn $name(s: &str, use_color: bool) -> String {
            if use_color {
                let $s = console::style(s);
                $styled.to_string()
            } else {
                s.to_string()
            }
        }
    };
}

color_fn!(color_pass, |s| s.bold().green());
color_fn!(color_fail, |s| s.bold().red());
color_fn!(color_error_token, |s| s.bold().magenta());
color_fn!(color_skip, |s| s.bold().yellow());
color_fn!(color_warn, |s| s.yellow());
color_fn!(color_timeout, |s| s.bright().yellow());
color_fn!(color_dim, |s| s.dim());
color_fn!(color_cyan, |s| s.cyan());
color_fn!(color_bold_white, |s| s.bold().white());
color_fn!(color_dim_green, |s| s.dim().green());
color_fn!(color_dim_cyan, |s| s.dim().cyan());
color_fn!(color_blue, |s| s.blue());
color_fn!(color_bold_cyan, |s| s.bold().cyan());
color_fn!(
    #[expect(dead_code)]
    color_green,
    |s| s.green()
);
