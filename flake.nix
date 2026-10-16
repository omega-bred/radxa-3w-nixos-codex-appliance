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
    } // nixpkgs.lib.optionalAttrs (arch == system) {
      board-thermal = pkgs.runCommand "board-thermal-tests" { nativeBuildInputs = [ pkgs.dtc ]; } ''
        dtb=${appliance.config.hardware.deviceTree.package}/rockchip/rk3566-radxa-zero-3w.dtb
        read -r provider clock_id < <(fdtget -t u "$dtb" /cpus/cpu@0 clocks)
        test "$provider" = "$(fdtget -t u "$dtb" /clock-controller@fdd20000 phandle)"
        test "$clock_id" = 10 # ARMCLK in rk3568-cru.h
        read -r cooling_cpu cooling_limits < <(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/cooling-maps/map0 cooling-device)
        test "$cooling_cpu" = "$(fdtget -t u "$dtb" /cpus/cpu@0 phandle)"
        test "$(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/trips/cpu_alert0 temperature)" = 70000
        read -r hot_cpu hot_min hot_max < <(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/cooling-maps/map1 cooling-device)
        test "$hot_cpu" = "$cooling_cpu"
        test "$hot_min $hot_max" = "6 6"
        test "$(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/cooling-maps/map1 trip)" = "$(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/trips/cpu_alert1 phandle)"
        test "$(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/trips/cpu_alert1 temperature)" = 75000
        test "$(fdtget -l "$dtb" /opp-table-0 | wc -l)" = 7
        test "$(fdtget -t u "$dtb" /thermal-zones/cpu-thermal/trips/cpu_crit temperature)" = 95000
        test "$(fdtget -t s "$dtb" /firmware/scmi status)" = disabled
        touch $out
      '';
    });
  };
}
