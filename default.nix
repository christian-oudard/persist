# Canonical Claude Code plugin directory for persist.
# Returns a single derivation that can be passed directly to
# programs.claude-code.plugins. The derivation contains:
#   .claude-plugin/plugin.json
#   skills/...
#   hooks/hooks.json
#   bin/persist   (symlink to the python package's bin/persist)
#
# Usage: plugins = [ (import persist { inherit pkgs; }) ];

{ pkgs }:

let
  persistPackage = pkgs.callPackage ./package.nix { };
in
pkgs.runCommand "persist-plugin" { } ''
  mkdir -p $out
  cp -r ${./.claude-plugin} $out/.claude-plugin
  cp -r ${./skills}        $out/skills
  cp -r ${./hooks}         $out/hooks
  mkdir -p $out/bin
  ln -s ${persistPackage}/bin/persist $out/bin/persist
''
