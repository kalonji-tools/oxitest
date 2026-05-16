{
  description = "oxitest — a fast Python test runner written in Rust";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python312;

        # Minimal Python interpreter — only pip for maturin editable installs.
        # All dev tools (ruff, ty, codespell, loguru, mkdocs, etc.) come from
        # `uv sync` so versions match pyproject.toml and CI.
        pythonEnv = python.withPackages (ps: with ps; [
          pip
        ]);

        oxitest = python.pkgs.callPackage ./nix/package.nix { };
      in
      {
        packages = {
          inherit oxitest;
          default = oxitest;
        };

        devShells.default = pkgs.mkShell {
          name = "oxitest-dev";

          packages = with pkgs; [
            # Rust toolchain
            cargo
            rustc
            clippy
            rustfmt
            rust-analyzer

            # Python interpreter + build tools
            pythonEnv
            maturin
            uv

            # Task runner & git hooks
            just
            prek
          ];

          env = {
            RUST_BACKTRACE = "1";
            # Tell PyO3 which Python to link against
            PYO3_PYTHON = "${pythonEnv}/bin/python3";
            # Ensure libpython is findable at link time
            LIBRARY_PATH = "${pythonEnv}/lib";
            # Prevent uv from downloading its own Python binaries — Nix owns Python
            UV_PYTHON_DOWNLOADS = "never";
          };

          shellHook = ''
            # Sync Python dev dependencies (ruff, ty, codespell, mkdocs, etc.)
            uv sync --quiet 2>/dev/null || true

            # Put .venv/bin on PATH so prek hooks find ruff, ty, codespell
            export PATH="$PWD/.venv/bin:$PATH"

            echo "oxitest dev shell ready"
            echo "  rust   : $(rustc --version)"
            echo "  python : $(python3 --version)"
            echo "  maturin: $(maturin --version)"
            echo "  uv     : $(uv --version)"
            echo "  ruff   : $(ruff --version)"
            echo "  ty     : $(ty --version)"
            echo "  prek   : $(prek --version)"

            # Install git hooks (unset core.hooksPath if set to avoid prek error)
            if git rev-parse --git-dir > /dev/null 2>&1; then
              git config --unset core.hooksPath 2>/dev/null || true
              prek install
            fi
          '';
        };
      });
}
