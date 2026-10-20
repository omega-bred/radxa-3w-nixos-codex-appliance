{ config, lib, pkgs, ... }:
let
  cfg = config.appliance.cellular;
  python = "${pkgs.python3}/bin/python3";
  chat = pkgs.writeText "sim7080.chat" ''
    ABORT 'BUSY'
    ABORT 'NO CARRIER'
    ABORT 'ERROR'
    TIMEOUT 30
    "" AT
    OK ATE0
    OK 'ATD*99#'
    CONNECT ""
  '';
  dnsUp = pkgs.writeShellScript "cellular-dns-up" ''
    {
      for server in "''${DNS1:-}" "''${DNS2:-}"; do
        if test -n "$server"; then printf 'nameserver %s\n' "$server"; fi
      done
    } | ${pkgs.openresolv}/bin/resolvconf -a "$1" -m ${toString cfg.routeMetric}
  '';
  dnsDown = pkgs.writeShellScript "cellular-dns-down" ''
    ${pkgs.openresolv}/bin/resolvconf -d "$1"
  '';
in {
  options.appliance.cellular = {
    enable = lib.mkEnableOption "Waveshare SIM7080G on the ZERO 3W GPIO header";
    apn = lib.mkOption {
      type = lib.types.strMatching "[A-Za-z0-9][A-Za-z0-9.-]{0,99}";
      description = "IPv4 APN supplied by the SIM provider.";
      example = "sensor.net";
    };
    autostart = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Connect LTE at boot. Disabled by default to conserve prepaid data.";
    };
    routeMetric = lib.mkOption {
      type = lib.types.ints.positive;
      default = 700;
      description = "LTE default-route metric; Wi-Fi normally uses 600.";
    };
  };

  config = lib.mkIf cfg.enable {
    # Pins 8/10 belong to the modem. An explicit alternate console also
    # overrides the DT's serial stdout-path on existing bootloaders.
    boot.kernelParams = [ "console=tty0" ];
    boot.loader.timeout = 0;
    # The stock U-Boot countdown can be interrupted by modem responses.
    # Existing installations need this bootloader flashed separately.
    system.build.cellularBootloader = (pkgs.ubootRadxaZero3W.override {
      extraConfig = "CONFIG_BOOTDELAY=-2";
    }).overrideAttrs (old: {
      postConfigure = (old.postConfigure or "") + ''
        grep -qx 'CONFIG_BOOTDELAY=-2' .config
      '';
    });
    systemd.services."serial-getty@ttyS2".enable = false;
    # Both interfaces may be up while Wi-Fi has the preferred default route.
    networking.firewall.checkReversePath = "loose";
    services.udev.extraRules = ''
      ACTION=="add|change", SUBSYSTEM=="tty", KERNEL=="ttyS2", ENV{ID_MM_DEVICE_IGNORE}="1"
      SUBSYSTEM=="gpio", KERNEL=="gpiochip3", TAG+="systemd"
    '';
    systemd.services.appliance-cellular-power = {
      description = "Hold SIM7080G PWRKEY released (physical pin 7)";
      wantedBy = [ "multi-user.target" ];
      requires = [ "dev-gpiochip3.device" ];
      after = [ "dev-gpiochip3.device" ];
      serviceConfig = {
        Type = "notify";
        ExecStart = "${python} ${../../cellular/power.py}";
        Restart = "on-failure";
        RestartSec = 5;
      };
    };
    services.pppd = {
      enable = true;
      peers.cellular = {
        inherit (cfg) autostart;
        config = ''
          /dev/ttyS2
          115200
          local
          nocrtscts
          noauth
          noipdefault
          ipcp-accept-local
          ipcp-accept-remote
          nodeflate
          nobsdcomp
          noipv6
          nodetach
          defaultroute
          defaultroute-metric ${toString cfg.routeMetric}
          usepeerdns
          noresolvconf
          ip-up-script ${dnsUp}
          ip-down-script ${dnsDown}
          lcp-echo-interval 30
          lcp-echo-failure 4
          connect "${pkgs.ppp}/bin/chat -f ${chat}"
        '';
      };
    };
    systemd.services.pppd-cellular = {
      # LTE registration must not hold up Wi-Fi, Tailscale, or SSH startup.
      before = lib.mkForce [];
      requires = [ "appliance-cellular-power.service" "dev-ttyS2.device" ];
      after = [ "network.target" "appliance-cellular-power.service" "dev-ttyS2.device" ];
      preStart = ''
        ${python} ${../../cellular/modem.py} --apn ${lib.escapeShellArg cfg.apn} --systemctl ${pkgs.systemd}/bin/systemctl
      '';
      serviceConfig = {
        TimeoutStartSec = 360;
        RestartSec = lib.mkForce 60;
        # openresolv manages the shared resolv.conf through its normal hooks.
        ReadWritePaths = [ "/etc/resolv.conf" "/run/resolvconf" ];
      };
    };
  };
}
