# The persist derivation, usable both as a Claude Code plugin directory
# (programs.claude-code.plugins) and as a package on PATH (home.packages).
# See package.nix for the combined layout.
#
# Usage (the same path serves both):
#   plugins       = [ (import persist { inherit pkgs; }) ];  # skills + Stop hook
#   home.packages = [ (import persist { inherit pkgs; }) ];  # persist on PATH
{ pkgs }:

pkgs.callPackage ./package.nix { }
