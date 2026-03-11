{
  description = "Persistent coding sessions for Claude Code using stop hooks";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      eachSystem = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
    in
    {
      packages = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in
        {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "persist";
            version = "0.1.0";
            src = ./.;
            format = "pyproject";
            nativeBuildInputs = [ pkgs.python3Packages.hatchling ];
          };
        }
      );

      # Skill paths for nix-claude consumers
      skills = {
        persist = ./skills/persist;
        persist-status = ./skills/persist-status;
        persist-stop = ./skills/persist-stop;
      };
    };
}
