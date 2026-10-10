{ config, lib, pkgs, modulesPath, ... }:
{
  imports = [ "${modulesPath}/installer/sd-card/sd-image.nix" ./wireless.nix ];
  nixpkgs.hostPlatform = "aarch64-linux";
  nixpkgs.config.allowUnfreePredicate = pkg: builtins.elem (lib.getName pkg) [ "rkbin" "rkbin-unstable" ];
  networking.usePredictableInterfaceNames = false;
  boot.loader.grub.enable = false;
  boot.loader.generic-extlinux-compatible.enable = true;
  boot.kernelPackages = pkgs.linuxPackages_6_12;
  boot.kernelParams = [ "console=ttyS2,1500000n8" ];
  # The AIC module supplies its own firmware; skip unrelated PC/GPU bundles.
  hardware.enableRedistributableFirmware = lib.mkForce false;
  hardware.wirelessRegulatoryDatabase = true;
  hardware.deviceTree = {
    enable = true;
    filter = "rk3566-radxa-zero-3w.dtb";
    overlays = [ { name = "headless-bluetooth"; dtsFile = ./headless-bluetooth.dts; } ];
  };
  sdImage = {
    compressImage = true;
    firmwarePartitionOffset = 16;
    firmwareSize = 16;
    populateFirmwareCommands = "";
    populateRootCommands = ''
      mkdir -p ./files/boot
      ${config.boot.loader.generic-extlinux-compatible.populateCmd} -c ${config.system.build.toplevel} -d ./files/boot -g 0
    '';
    postBuildCommands = ''
      test $(stat -c%s ${pkgs.ubootRadxaZero3W}/u-boot-rockchip.bin) -lt $((16 * 1024 * 1024 - 32768))
      dd if=${pkgs.ubootRadxaZero3W}/u-boot-rockchip.bin of=$img bs=512 seek=64 conv=notrunc
    '';
  };
  # The SDIO driver uploads the combo chip's Bluetooth firmware too.
  systemd.services.radxa-bluetooth = {
    description = "Radxa AIC8800 UART Bluetooth";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-modules-load.service" "dev-ttyS1.device" ];
    requires = [ "dev-ttyS1.device" ];
    before = [ "bluetooth.service" ];
    serviceConfig = {
      ExecStart = "${pkgs.bluez}/bin/hciattach -n /dev/ttyS1 any 1500000 flow";
      Restart = "always";
      RestartSec = 5;
    };
  };
}
