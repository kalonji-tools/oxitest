fn main() {
    // Embed git short hash — falls back to "unknown" outside a git repo.
    let git_hash = std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=GIT_HASH={git_hash}");

    // Embed rustc version string, stripping the leading "rustc " prefix.
    // Result is e.g. "1.87.0 (17067e9ac 2025-05-09)".
    let rustc_ver = std::process::Command::new("rustc")
        .arg("--version")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".to_string());
    let rustc_ver = rustc_ver
        .strip_prefix("rustc ")
        .unwrap_or(&rustc_ver)
        .to_string();
    println!("cargo:rustc-env=RUSTC_VERSION={rustc_ver}");

    // Rerun only when the git HEAD changes.
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/refs/heads/");
}
