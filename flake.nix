{
  description = "oxitest – a fast Python test runner written in Rust";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python312;
      version = (builtins.fromTOML (builtins.readFile ./Cargo.toml)).package.version;

      # Build the maturin wheel from source
      oxitest-wheel = pkgs.rustPlatform.buildRustPackage {
        pname = "oxitest-wheel";
        inherit version;
        src = self;
        cargoLock.lockFile = ./Cargo.lock;
        nativeBuildInputs = [ pkgs.maturin python ];
        buildPhase = ''
          maturin build --release --interpreter ${python}/bin/python3
        '';
        installPhase = ''
          mkdir -p $out
          cp target/wheels/*.whl $out/
        '';
        doCheck = false;
      };

      # Install the wheel into a Python environment
      oxitest = python.pkgs.buildPythonPackage {
        pname = "oxitest";
        inherit version;
        format = "other";
        dontUnpack = true;
        dontBuild = true;
        nativeBuildInputs = [ python.pkgs.installer ];
        installPhase = ''
          ${python}/bin/python3 -m installer --destdir="$out" --prefix="" ${oxitest-wheel}/*.whl
        '';
        pythonImportsCheck = [ "oxitest" ];
      };
    in
    {
      packages.${system}.default = oxitest;
    };
}
