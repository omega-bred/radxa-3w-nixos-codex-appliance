{ config, lib, pkgs, ... }:
let
  cfg = config.appliance;
  python = pkgs.python3.withPackages (p: [ p.dbus-python p.pygobject3 ]);
in {
  options.appliance = {
    wifiInterface = lib.mkOption { type = lib.types.str; default = "wlan0"; };
    updateFlake = lib.mkOption {
      type = lib.types.str;
      default = "git+https://github.com/omega-bred/radxa-3w-nixos-codex-appliance?ref=stable#radxa-zero3w";
      description = "Stable repository ref and board configuration used for updates.";
    };
  };

  config = {
    system.stateVersion = "25.11";
    networking.hostName = lib.mkDefault "codex";
    networking.networkmanager.enable = true;
    networking.networkmanager.wifi.powersave = false;
    networking.useDHCP = false;
    networking.firewall = {
      allowedTCPPorts = [ 22 ];
      interfaces.${cfg.wifiInterface} = {
        allowedTCPPorts = [ 80 ];
        allowedUDPPorts = [ 53 67 ];
      };
    };
    services.openssh = {
      enable = true;
      authorizedKeysFiles = [ "/var/lib/appliance-keys/%u" ];
      settings = { PasswordAuthentication = false; PermitRootLogin = "no"; };
    };
    services.avahi = { enable = true; nssmdns4 = true; publish.enable = true; publish.addresses = true; };
    services.tailscale.enable = true;
    networking.firewall.allowedUDPPorts = [ config.services.tailscale.port ];
    hardware.bluetooth = { enable = true; powerOnBoot = true; };

    users.users.codex = {
      isNormalUser = true;
      home = "/var/lib/codex";
      createHome = true;
    };
    users.users.admin = { isNormalUser = true; extraGroups = [ "wheel" ]; };
    security.sudo.wheelNeedsPassword = false;
    systemd.tmpfiles.rules = [ "d /var/lib/appliance-keys 0755 root root -" ];
    environment.systemPackages = [ pkgs.codex pkgs.gitMinimal pkgs.ripgrep ];
    nix.settings.experimental-features = [ "nix-command" "flakes" ];
    nix.gc = { automatic = true; dates = "weekly"; options = "--delete-older-than 14d"; };
    boot.loader.generic-extlinux-compatible.configurationLimit = 3;
    zramSwap.enable = true;
    services.journald.extraConfig = "SystemMaxUse=64M";

    systemd.services.codex-provision = {
      description = "Codex Wi-Fi and BLE provisioning";
      wantedBy = [ "multi-user.target" ];
      wants = [ "NetworkManager.service" "bluetooth.service" "tailscaled.service" ];
      after = [ "NetworkManager.service" "bluetooth.service" "tailscaled.service" ];
      path = [ pkgs.networkmanager pkgs.systemd pkgs.util-linux pkgs.codex pkgs.tailscale ];
      environment = {
        WIFI_INTERFACE = cfg.wifiInterface;
        PROVISION_WEB = "${../provision/index.html}";
      };
      serviceConfig = {
        ExecStart = "${python}/bin/python ${../provision/service.py}";
        Restart = "always";
        RestartSec = 5;
        StateDirectory = "codex-provision";
        StateDirectoryMode = "0700";
        UMask = "0077";
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = [ "/var/lib/codex" "/var/lib/appliance-keys" "/var/lib/NetworkManager" "/etc/NetworkManager/system-connections" ];
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };
    systemd.services.codex-hostname = {
      description = "Restore provisioned hostname";
      wantedBy = [ "multi-user.target" ];
      before = [ "codex-provision.service" "avahi-daemon.service" ];
      serviceConfig.Type = "oneshot";
      script = ''
        if [ -f /var/lib/codex-provision/hostname ]; then
          ${pkgs.systemd}/bin/hostnamectl --transient set-hostname "$(cat /var/lib/codex-provision/hostname)"
        fi
      '';
    };
    system.autoUpgrade = {
      enable = true;
      flake = cfg.updateFlake;
      flags = [ "--refresh" ];
      dates = "daily";
      randomizedDelaySec = "1h";
      persistent = true;
      allowReboot = true;
      rebootWindow = { lower = "03:00"; upper = "05:00"; };
    };
  };
}
