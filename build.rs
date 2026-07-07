use std::process::Command;

/// Resolves the git SHA for this build.
///
/// Prefers the `GIT_SHA` env var (set via `--build-arg` in container builds,
/// where the `.git` directory isn't present in the build context) and falls
/// back to invoking `git` directly for local `cargo build`/`cargo run`.
fn git_sha() -> String {
    std::env::var("GIT_SHA")
        .ok()
        .filter(|sha| !sha.is_empty())
        .or_else(|| {
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .output()
                .ok()
                .filter(|output| output.status.success())
                .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        })
        .unwrap_or_else(|| "unknown".to_string())
}

/// Resolves the UTC build timestamp using the `date` binary, which is
/// available in the container builder image and on developer machines.
fn build_date() -> String {
    Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_else(|| "unknown".to_string())
}

fn main() {
    println!("cargo:rustc-env=GIT_SHA={}", git_sha());
    println!("cargo:rustc-env=BUILD_DATE={}", build_date());
    println!("cargo:rerun-if-env-changed=GIT_SHA");
    println!("cargo:rerun-if-changed=.git/HEAD");
}
