{
  lib,
  buildPythonPackage,
  fetchFromGitHub,
  rustPlatform,
  writeText,
  runCommand,
}:

buildPythonPackage rec {
  pname = "oxitest";
  version = "1.0.0";
  pyproject = true;

  src = fetchFromGitHub {
    owner = "kalonji-tools";
    repo = "oxitest";
    rev = "v${version}";
    hash = "sha256-X4xRXqXXsc8bGydHGk3w2j/klL/TXa7tEKt42kHTQGk=";
  };

  cargoDeps = rustPlatform.fetchCargoVendor {
    inherit src;
    name = "${pname}-${version}";
    hash = "sha256-nBEmgsU7WDIsy32UclzwqHz10KAMlQ5htZ3+jgPsUnc=";
  };

  nativeBuildInputs = with rustPlatform; [
    cargoSetupHook
    maturinBuildHook
  ];

  doCheck = false;

  pythonImportsCheck = [ "oxitest" ];

  passthru.tests =
    let
      oxitest = buildPythonPackage {
        inherit
          pname
          version
          src
          cargoDeps
          pyproject
          nativeBuildInputs
          ;
      };
    in
    {
      version = runCommand "${pname}-version-test" { } ''
        ${lib.getExe oxitest} --version | grep -q "${version}"
        touch $out
      '';
      smoke = runCommand "${pname}-smoke-test" { } ''
        ${lib.getExe oxitest} ${
          writeText "test_smoke.py" ''
            def test_smoke():
                assert 1 + 1 == 2, "basic arithmetic should work"
          ''
        }
        touch $out
      '';
    };

  meta = {
    description = "A fast Python test runner written in Rust";
    homepage = "https://github.com/kalonji-tools/oxitest";
    changelog = "https://github.com/kalonji-tools/oxitest/blob/v${version}/CHANGELOG.md";
    license = lib.licenses.mit;
    maintainers = [ ]; # Add nixpkgs maintainer handle when upstreaming
    platforms = lib.platforms.unix;
    mainProgram = "oxitest";
  };
}
