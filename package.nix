# A single derivation that is both the persist executable and a canonical
# Claude Code plugin directory. $out holds bin/persist (from the Python
# build) plus .claude-plugin/, skills/, and hooks/, so the same path can be
# passed to programs.claude-code.plugins (registers skills and the Stop
# hook) and to home.packages (puts persist on the login PATH, which hooks
# and a plain terminal inherit).
{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "persist";
  version = "0.1.0";
  src = ./.;
  format = "pyproject";
  nativeBuildInputs = [ python3Packages.hatchling ];
  postInstall = ''
    cp -r .claude-plugin skills hooks $out/
  '';
}
