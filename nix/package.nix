{ lib
, buildPythonPackage
, fetchPypi
, rustPlatform
, cargo
, rustc
}:

buildPythonPackage rec {
  pname = "oxitest";
  version = "0.1.0";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-M1iNSEDWzF+XU0gogdLratjJcw1DPaT6IbwOCfUTcRU=";
  };

  cargoDeps = rustPlatform.fetchCargoVendor {
    inherit src;
    name = "${pname}-${version}";
    hash = "sha256-7oCPrRH+3LzhNDsHfouMq8cNJH3QXlOGsSua7dwuPTA=";
  };

  nativeBuildInputs = [
    cargo
    rustPlatform.cargoSetupHook
    rustc
  ];

  build-system = [
    rustPlatform.maturinBuildHook
  ];

  meta = with lib; {
    description = "A fast Python test runner written in Rust";
    homepage = "https://github.com/kalonji-tools/oxitest";
    license = licenses.mit;
    mainProgram = "oxitest";
    maintainers = [ ];
    platforms = platforms.unix;
  };
}
