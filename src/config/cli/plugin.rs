/// Append plugin-declared CLI args to an existing clap Command.
pub fn add_plugin_args(
    mut cmd: clap::Command,
    extensions: &super::super::PluginCliExtensions,
) -> clap::Command {
    use clap::Arg;

    for (prefix, options) in extensions.plugins.values() {
        for opt in options {
            let id: clap::builder::Str = format!("{}.{}", prefix, opt.field_name).into();
            let long: clap::builder::Str =
                opt.long_flag.trim_start_matches("--").to_string().into();
            let mut arg = Arg::new(id.clone()).long(long).help(opt.help.clone());

            if let Some(short) = opt.short_flag {
                arg = arg.short(short);
            }

            match opt.value_type {
                super::super::PluginValueType::Bool => {
                    arg = arg.action(clap::ArgAction::SetTrue);
                }
                _ => {
                    arg = arg.action(clap::ArgAction::Set);
                }
            }

            if let Some(ref env_var) = opt.env {
                let env_os: std::ffi::OsString = env_var.into();
                arg = arg.env(env_os);
            }

            cmd = cmd.arg(arg);
        }
    }

    cmd
}

/// Extract plugin CLI values from parsed matches.
pub fn extract_plugin_values(
    matches: &clap::ArgMatches,
    extensions: &super::super::PluginCliExtensions,
) -> std::collections::HashMap<String, std::collections::HashMap<String, toml::Value>> {
    let mut result = std::collections::HashMap::new();

    for (module, (prefix, options)) in &extensions.plugins {
        let mut values = std::collections::HashMap::new();
        for opt in options {
            let id = format!("{}.{}", prefix, opt.field_name);
            match opt.value_type {
                super::super::PluginValueType::Bool => {
                    if matches.get_flag(&id) {
                        values.insert(opt.field_name.clone(), toml::Value::Boolean(true));
                    }
                }
                super::super::PluginValueType::Int => {
                    if let Some(s) = matches.get_one::<String>(&id)
                        && let Ok(n) = s.parse::<i64>()
                    {
                        values.insert(opt.field_name.clone(), toml::Value::Integer(n));
                    }
                }
                super::super::PluginValueType::Float => {
                    if let Some(s) = matches.get_one::<String>(&id)
                        && let Ok(n) = s.parse::<f64>()
                    {
                        values.insert(opt.field_name.clone(), toml::Value::Float(n));
                    }
                }
                super::super::PluginValueType::String => {
                    if let Some(val) = matches.get_one::<String>(&id) {
                        values.insert(opt.field_name.clone(), toml::Value::String(val.clone()));
                    }
                }
            }
        }
        if !values.is_empty() {
            result.insert(module.clone(), values);
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{PluginCliExtensions, PluginCliOption, PluginValueType};
    use std::collections::HashMap;

    fn make_extensions() -> PluginCliExtensions {
        let mut plugins = HashMap::new();
        plugins.insert(
            "my_plugin".to_string(),
            (
                "myplugin".to_string(),
                vec![
                    PluginCliOption {
                        field_name: "host".to_string(),
                        long_flag: "--myplugin-host".to_string(),
                        short_flag: Some('H'),
                        help: "Target host".to_string(),
                        value_type: PluginValueType::String,
                        env: None,
                        has_default: true,
                    },
                    PluginCliOption {
                        field_name: "verbose".to_string(),
                        long_flag: "--myplugin-verbose".to_string(),
                        short_flag: None,
                        help: "Verbose output".to_string(),
                        value_type: PluginValueType::Bool,
                        env: None,
                        has_default: true,
                    },
                ],
            ),
        );
        PluginCliExtensions { plugins }
    }

    #[test]
    fn add_plugin_args_adds_string_flag() {
        let cmd = clap::Command::new("test");
        let ext = make_extensions();
        let cmd = add_plugin_args(cmd, &ext);
        let matches = cmd
            .try_get_matches_from(["test", "--myplugin-host", "ssh://host"])
            .expect("should parse plugin string flag");
        let val: &String = matches
            .get_one("myplugin.host")
            .expect("host should be present");
        assert_eq!(val, "ssh://host");
    }

    #[test]
    fn add_plugin_args_adds_bool_flag() {
        let cmd = clap::Command::new("test");
        let ext = make_extensions();
        let cmd = add_plugin_args(cmd, &ext);
        let matches = cmd
            .try_get_matches_from(["test", "--myplugin-verbose"])
            .expect("should parse plugin bool flag");
        assert!(matches.get_flag("myplugin.verbose"));
    }

    #[test]
    fn add_plugin_args_short_flag_works() {
        let cmd = clap::Command::new("test");
        let ext = make_extensions();
        let cmd = add_plugin_args(cmd, &ext);
        let matches = cmd
            .try_get_matches_from(["test", "-H", "docker://c"])
            .expect("should parse short flag");
        let val: &String = matches
            .get_one("myplugin.host")
            .expect("host should be present via short flag");
        assert_eq!(val, "docker://c");
    }

    #[test]
    fn extract_plugin_values_extracts_string() {
        let ext = make_extensions();
        let cmd = add_plugin_args(clap::Command::new("test"), &ext);
        let matches = cmd
            .try_get_matches_from(["test", "--myplugin-host", "val"])
            .unwrap();
        let values = extract_plugin_values(&matches, &ext);
        let plugin_vals = values
            .get("my_plugin")
            .expect("my_plugin should have values");
        assert_eq!(
            plugin_vals.get("host"),
            Some(&toml::Value::String("val".to_string()))
        );
    }

    #[test]
    fn extract_plugin_values_extracts_bool() {
        let ext = make_extensions();
        let cmd = add_plugin_args(clap::Command::new("test"), &ext);
        let matches = cmd
            .try_get_matches_from(["test", "--myplugin-verbose"])
            .unwrap();
        let values = extract_plugin_values(&matches, &ext);
        let plugin_vals = values
            .get("my_plugin")
            .expect("my_plugin should have values");
        assert_eq!(
            plugin_vals.get("verbose"),
            Some(&toml::Value::Boolean(true))
        );
    }

    #[test]
    fn extract_plugin_values_omits_unset_args() {
        let ext = make_extensions();
        let cmd = add_plugin_args(clap::Command::new("test"), &ext);
        let matches = cmd.try_get_matches_from(["test"]).unwrap();
        let values = extract_plugin_values(&matches, &ext);
        assert!(values.is_empty(), "no plugin args passed, should be empty");
    }

    #[test]
    fn add_plugin_args_empty_extensions() {
        let cmd = clap::Command::new("test");
        let ext = PluginCliExtensions::default();
        let cmd = add_plugin_args(cmd, &ext);
        let matches = cmd.try_get_matches_from(["test"]).unwrap();
        let values = extract_plugin_values(&matches, &ext);
        assert!(
            values.is_empty(),
            "empty extensions should produce no values"
        );
    }
}
