{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "persist";
  version = "0.1.0";
  src = ./.;
  format = "pyproject";
  nativeBuildInputs = [ python3Packages.hatchling ];
}
