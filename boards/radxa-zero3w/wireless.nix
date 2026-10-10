# Adapted from icefirex/nixos-radxa-zero3w (MIT); keep kernel and driver in step.
{ config, pkgs, ... }:
let
  src = pkgs.fetchFromGitHub {
    owner = "radxa-pkg";
    repo = "aic8800";
    rev = "c9176c164b3dd154d8fc7ae23c5f0cfd6b6553a3";
    hash = "sha256-ZamZ+nerZRFiHxaLx9x5vnRaATKvY7FkBsfkzsdL3wc=";
  };
  firmware = pkgs.stdenvNoCC.mkDerivation {
    pname = "aic8800-sdio-firmware";
    version = "2024-11-19";
    inherit src;
    dontBuild = true;
    installPhase = ''
      mkdir -p $out/lib/firmware/aic8800
      cp src/SDIO/driver_fw/fw/aic8800D80/* $out/lib/firmware/aic8800/
    '';
  };
  driver = config.boot.kernelPackages.callPackage ({ stdenv, kernel }: stdenv.mkDerivation {
    pname = "aic8800-sdio";
    version = "2024-11-19-${kernel.version}";
    inherit src;
    nativeBuildInputs = kernel.moduleBuildDependencies;
    hardeningDisable = [ "pic" ];
    patches = [ ./power-sequence.patch ];
    postPatch = ''
      for p in fix-linux-6.1-build fix-linux-6.5-build fix-linux-6.7-build \
               fix-linux-6.9-build fix-linux-6.12-build fix-sdio-firmware-path fix-sdio-fall-through; do
        patch -p1 < debian/patches/$p.patch
      done
    '';
    preBuild = "cd src/SDIO/driver_fw/driver/aic8800";
    makeFlags = [ "KDIR=${kernel.dev}/lib/modules/${kernel.modDirVersion}/build" "ARCH=arm64" "CROSS_COMPILE=${stdenv.cc.targetPrefix}" ];
    installPhase = ''
      mkdir -p $out/lib/modules/${kernel.modDirVersion}/extra
      cp aic8800_bsp/*.ko aic8800_fdrv/*.ko $out/lib/modules/${kernel.modDirVersion}/extra/
    '';
  }) {};
in {
  boot.extraModulePackages = [ driver ];
  boot.kernelModules = [ "aic8800_bsp" "aic8800_fdrv" "hci_uart" ];
  boot.extraModprobeConfig = ''
    options aic8800_bsp aic_fw_path=${firmware}/lib/firmware/aic8800 skip_power_wait=1
    options aic8800_fdrv aicwf_dbg_level=0
  '';
}
