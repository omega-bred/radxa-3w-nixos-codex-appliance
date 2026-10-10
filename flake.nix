{
  description = "A small NixOS Codex appliance";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs = { self, nixpkgs }: let
    system = "aarch64-linux";
    appliance = nixpkgs.lib.nixosSystem {
      inherit system;
      modules = [
        ./boards/radxa-zero3w
        ./modules/appliance.nix
        { system.configurationRevision = self.rev or "dirty"; }
      ];
    };
  in {
    nixosModules.appliance = import ./modules/appliance.nix;
    nixosConfigurations.radxa-zero3w = appliance;
    packages.${system} = {
      sdImage = appliance.config.system.build.sdImage;
      default = self.packages.${system}.sdImage;
    };
    checks = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (arch: let
      pkgs = import nixpkgs { system = arch; };
    in {
      provisioning = pkgs.runCommand "provisioning-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
        cp -r ${./provision} provision
        cp ${./tests/test_provision.py} test_provision.py
        python -m unittest -v test_provision
        touch $out
      '';
    });
  };
}
