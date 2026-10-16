{ config, lib, ... }:
let
  cfg = config.appliance.npu;
  driver = config.boot.kernelPackages.callPackage ../../packages/rknpu { };
in {
  options.appliance.npu = {
    enable = lib.mkEnableOption "experimental RK3566 NPU support";
    autoload = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Load the NPU driver automatically; disable for the first hardware trial.";
    };
    memoryMiB = lib.mkOption {
      type = lib.types.ints.between 64 2048;
      default = 128;
      description = "Contiguous shared RAM available for NPU weights and working buffers, in MiB.";
    };
    maxFrequencyMHz = lib.mkOption {
      type = lib.types.enum [ 200 400 ];
      default = 200;
      description = "NPU clock ceiling; 200 MHz is the conservative hardware trial setting.";
    };
  };
  config = lib.mkIf cfg.enable {
    boot.extraModulePackages = [ driver ];
    boot.kernelModules = lib.optional cfg.autoload "rknpu";
    boot.blacklistedKernelModules = lib.optional (!cfg.autoload) "rknpu";
    boot.kernelParams = [ "cma=${toString cfg.memoryMiB}M" ];
    hardware.deviceTree.overlays = [ { name = "rk3566-npu"; dtsFile = ./npu.dts; } ];
    systemd.services.rknpu-clock-limit = lib.mkIf cfg.autoload {
      description = "Apply the RK3566 NPU clock ceiling";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-modules-load.service" ];
      before = [ "qwen-local.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        echo ${toString (cfg.maxFrequencyMHz * 1000000)} > /sys/class/devfreq/fde40000.npu/max_freq
      '';
    };
  };
}
