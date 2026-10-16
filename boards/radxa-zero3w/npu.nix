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
    minFrequencyMHz = lib.mkOption {
      type = lib.types.enum [ 200 400 ];
      default = 200;
      description = "Minimum active NPU clock; idle power gating remains controlled by the driver.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [ {
      assertion = cfg.minFrequencyMHz <= cfg.maxFrequencyMHz;
      message = "The NPU minimum clock must not exceed its maximum clock.";
    } ];
    boot.extraModulePackages = [ driver ];
    boot.kernelModules = lib.optional cfg.autoload "rknpu";
    boot.blacklistedKernelModules = lib.optional (!cfg.autoload) "rknpu";
    boot.kernelParams = [ "cma=${toString cfg.memoryMiB}M" ];
    hardware.deviceTree.overlays = [ { name = "rk3566-npu"; dtsFile = ./npu.dts; } ];
    systemd.services.rknpu-clock-limit = lib.mkIf cfg.autoload {
      description = "Apply the RK3566 NPU clock range";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-modules-load.service" ];
      before = [ "qwen-local.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        # Lower an old floor before applying a potentially lower ceiling.
        echo 200000000 > /sys/class/devfreq/fde40000.npu/min_freq
        echo ${toString (cfg.maxFrequencyMHz * 1000000)} > /sys/class/devfreq/fde40000.npu/max_freq
        echo ${toString (cfg.minFrequencyMHz * 1000000)} > /sys/class/devfreq/fde40000.npu/min_freq
      '';
    };
  };
}
