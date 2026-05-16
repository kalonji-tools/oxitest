use super::WorkerCount;
use serde::Deserialize;

#[derive(Deserialize, Default)]
pub(super) struct PyprojectToml {
    pub(super) tool: Option<ToolTable>,
}

#[derive(Deserialize, Default)]
pub(super) struct ToolTable {
    pub(super) oxitest: Option<OxitestConfig>,
}

#[derive(Deserialize, Default)]
pub(super) struct OxitestConfig {
    pub(super) testpaths: Option<Vec<String>>,
    pub(super) python_files: Option<Vec<String>>,
    pub(super) norecursedirs: Option<Vec<String>>,
    pub(super) markers: Option<Vec<String>>,
    pub(super) timeout: Option<u64>,
    pub(super) cache_max_age: Option<u32>,
    pub(super) min_parallel_tests: Option<usize>,
    pub(super) timeout_multiplier: Option<f64>,
    pub(super) spawn_overhead_ms: Option<f64>,
    pub(super) strict: Option<super::StrictMode>,
    pub(super) workers: Option<WorkerCount>,
    pub(super) schedule: Option<super::ScheduleStrategy>,
    pub(super) failed: Option<super::FailedMode>,
    pub(super) tb: Option<super::TbStyle>,
    pub(super) verbose: Option<bool>,
    pub(super) maxfail: Option<usize>,
    pub(super) durations: Option<usize>,
    pub(super) serial: Option<bool>,
    pub(super) color: Option<super::ColorMode>,
}

impl<'de> serde::Deserialize<'de> for WorkerCount {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct WorkerCountVisitor;

        impl<'de> serde::de::Visitor<'de> for WorkerCountVisitor {
            type Value = WorkerCount;

            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("\"auto\" or a positive integer")
            }

            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<WorkerCount, E> {
                if v.eq_ignore_ascii_case("auto") {
                    Ok(WorkerCount::Auto)
                } else {
                    Err(E::custom(format!("expected \"auto\", got \"{v}\"")))
                }
            }

            fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<WorkerCount, E> {
                if v <= 0 {
                    Err(E::custom("worker count must be at least 1"))
                } else {
                    Ok(WorkerCount::Fixed(v as usize))
                }
            }

            fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<WorkerCount, E> {
                if v == 0 {
                    Err(E::custom("worker count must be at least 1"))
                } else {
                    Ok(WorkerCount::Fixed(v as usize))
                }
            }
        }

        deserializer.deserialize_any(WorkerCountVisitor)
    }
}
