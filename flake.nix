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

        pythonEnv = python.withPackages (ps: with ps; [
          pytest
          pip
          loguru
          mkdocs-material
          mkdocs-git-revision-date-localized-plugin
          mkdocs-minify-plugin
          mkdocstrings
          mkdocstrings-python
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          name = "oxitest-dev";

          packages = with pkgs; [
            # Rust toolchain
            cargo
            rustc
            clippy
            rustfmt
            rust-analyzer

            # Build support
            pkg-config
            openssl

            # Python + tooling
            pythonEnv
            maturin
            uv

            # Linting
            ruff
            ty
            codespell

            # Task runner
            just

            # Docs (mkdocs-material and plugins are in pythonEnv above)

            # Git hooks
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
