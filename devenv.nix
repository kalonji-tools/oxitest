{ config, pkgs, ... }:

let
  python = pkgs.python312;
  pythonEnv = python.withPackages (ps: [ ps.pip ]);
in
{
  languages.rust = {
    enable = true;
    toolchainFile = ./rust-toolchain.toml;

    # devenv derives RUST_SRC_PATH from `toolchain.rust-src` and falls back to
    # nixpkgs' rustLibSrc when it is unset — and `toolchainFile` never sets it,
    # so rust-analyzer would index a standard library from a different release
    # than the rustc beside it. That is the drift #1792 exists to remove, so
    # point it back at the pinned toolchain, which carries its own `rust-src`.
    toolchain.rust-src = config.languages.rust.toolchainPackage;
  };

  languages.python = {
    enable = true;
    package = pythonEnv;
    uv = {
      enable = true;
      sync.enable = true;
    };
  };

  packages = with pkgs; [
    # Python build tool
    maturin

    # Task runner & git hooks
    just
    prek
    # Snapshot testing (matches CI's cargo-insta)
    cargo-insta

    # Benchmarking
    hyperfine

    # File watcher
    bacon

    # Internals book
    mdbook
    mdbook-mermaid

    # GitHub Actions workflow linter (#1974)
    actionlint

    # Release tooling
    git-cliff

    # Interactive query browsing
    fzf
  ];

  env = {
    RUST_BACKTRACE = "1";

    # Tell PyO3 which Python to link against
    PYO3_PYTHON = "${pythonEnv}/bin/python3";
  };

  tasks."oxitest:install-hooks" = {
    exec = ''
      if git rev-parse --git-dir > /dev/null 2>&1; then
        git config --unset core.hooksPath 2>/dev/null || true
        prek install --quiet
        HOOKS_DIR="$(git rev-parse --git-common-dir)/hooks"
        git config core.hooksPath "$HOOKS_DIR"
      fi
    '';
    before = [ "devenv:enterShell" ];
  };

  tasks."oxitest:install-graphify" = {
    exec = ''
      uv tool install graphifyy -q 2>/dev/null
      export PATH="$HOME/.local/bin:$PATH"
      if git rev-parse --git-dir > /dev/null 2>&1; then
        graphify hook install 2>/dev/null || true
      fi
    '';
    before = [ "devenv:enterShell" ];
  };

  enterShell = ''
    # Put venv bin and uv tools on PATH so prek hooks find ruff, ty, codespell, graphify
    export PATH="$HOME/.local/bin:$UV_PROJECT_ENVIRONMENT/bin:$PATH"

    # Symlink .venv → devenv venv so tools with hardcoded .venv paths work
    # (e.g. ty's python = ".venv" in pyproject.toml)
    if [ "$UV_PROJECT_ENVIRONMENT" != "$PWD/.venv" ] && [ ! -L .venv ]; then
      ln -snf "$UV_PROJECT_ENVIRONMENT" .venv
    fi

    # Auto-build Rust extension if missing or stale
    PY_VER=$(python3 -c 'import sys; print(f"cpython-{sys.version_info[0]}{sys.version_info[1]}")')
    SO=$(find python/oxitest -name "_oxitest.$PY_VER*.so" 2>/dev/null | head -1)
    if [ -z "$SO" ]; then
      echo "Building Rust extension..."
      just build
    elif [ -n "$(find src -name '*.rs' -newer "$SO" 2>/dev/null | head -1)" ] || \
         [ Cargo.toml -nt "$SO" ]; then
      echo "Rust source changed, rebuilding extension..."
      just build
    fi

    just health
  '';
}
