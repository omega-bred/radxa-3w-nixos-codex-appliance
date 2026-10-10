# Codex appliance

A headless NixOS SD-card image for the **Radxa ZERO 3W (RK3566, AIC8800)**.
Includes Codex CLI, key-only SSH, Wi-Fi/BLE provisioning, Tailscale/Headscale,
and daily system updates. No desktop, default SSH password, or secrets in the
flake. Use a good power supply and a 16 GB or larger microSD card.

## Build and boot

On an ARM64 Linux Nix builder (or an x86 builder with aarch64 binfmt):

```sh
nix build .#sdImage --system aarch64-linux
zstd -d result/sd-image/*.img.zst -o codex.img
```

Flash `codex.img` with Etcher or another image writer, insert the card and power
on. U-Boot is embedded; no separate bootloader flash is needed. The root partition
expands on first boot. GitHub Actions also builds an image artifact on ARM64.

## Provision

1. Join `codex-setup-<device-id>` with password **`codex-setup`** and open
   **http://10.42.0.1**. Set the hostname, Wi-Fi credentials and optionally an SSH
   public key. Save the returned provisioning token before leaving the page.
2. After a successful Wi-Fi connection the hotspot is permanently disabled.
   It returns after a failed first attempt; it never returns just because a
   previously configured network is unavailable. BLE remains advertised as
   **Codex setup** for configuration and recovery.
3. To use the same page over BLE, serve it on your laptop:

   ```sh
   python3 -m http.server 8000 --directory provision
   ```

   Open http://localhost:8000 on that laptop in a Web Bluetooth browser (for
   example Chrome on macOS/Windows), click **Connect over Bluetooth**,
   pair when prompted, and enter the saved token. You can also do the entire
   first setup over BLE. Web Bluetooth needs a secure context; the hotspot's
   HTTP page supports Wi-Fi setup, while localhost enables BLE.
4. Click **Sign in to Codex**, then **Refresh status and login links**. Open the
   returned device-login URL on your phone/laptop and enter the code. Device
   authorization must be enabled for your ChatGPT account. Credentials stay in
   `/var/lib/codex/.codex`, owned by the `codex` user.
5. Click **Connect Tailscale**, refresh status and open its enrollment URL. For
   Headscale, enter your control-server URL and optional device name first;
   approve the node using that deployment's enrollment flow. Tailscale identity
   persists under `/var/lib/tailscale`; DNS stays with the local network.

Once provisioned, run `ssh codex@<hostname>.local` (or its LAN/Tailscale IP),
then `codex`. Use `ssh admin@<hostname>.local` for maintenance with `sudo`.
The SSH key is optional but needed for remote shell access. Codex
runs as an unprivileged user; provisioning and updates run as system services.

First setup trusts nearby users: the initial AP password is public and BLE uses
encrypted Just Works pairing. Provision in a trusted place. After the first
claim, every API request needs the random token. Wi-Fi passwords go into root-only
NetworkManager profiles, and HTTP listens only on the initial hotspot address.
Wi-Fi supports open networks and WPA2 Personal (including WPA2/WPA3 mixed mode).

### BLE API

Service UUID: `d93f0001-7b2c-4e2a-9e74-41c868b36f90`.
Read/write characteristic: `d93f0002-7b2c-4e2a-9e74-41c868b36f90`.
Send UTF-8 JSON ending in a newline, in chunks of at most 180 bytes, then read
the JSON response (long reads supported). Responses belong to the connected
device. Pairing is required; requests are limited to 4096 bytes.

```json
{"op":"configure","hostname":"codex-one","ssid":"Home","password":"wifi-password","ssh_key":"ssh-ed25519 …","token":"…"}
{"op":"login","token":"…"}
{"op":"tailscale","login_server":"https://headscale.example.com","hostname":"codex-one","token":"…"}
{"op":"status","token":"…"}
```

Omit `token` for the first configuration. `configure` returns the token before
switching Wi-Fi; poll `status` for completion/errors. Tailscale's server defaults
to the hosted service and its name defaults to the system hostname. Both login
operations are asynchronous; refresh status for their links and exit codes.

## Updates and development

**`main` is development; `stable` is the deployment branch.** Devices rebuild
from `stable` daily with a randomized delay, using its committed `flake.lock`.
Kernel changes can reboot between 03:00 and 05:00 device time (UTC by default).
Failed builds leave the current system running; the boot menu retains three
generations. Updating U-Boot itself requires reflashing the SD image.

```sh
nix flake check
nix build .#sdImage --system aarch64-linux
# After CI and a real-board smoke test, promote the reviewed commit:
git push origin main:stable
```

Update dependencies deliberately with `nix flake update`; device updates don't
float dependencies independently. The update repository must be readable by
devices (public, or with separately configured Git credentials).

`modules/appliance.nix` holds shared services; `boards/radxa-zero3w/` holds the
kernel, drivers, device tree and image layout. Add another board module and flake
configuration, and override `appliance.wifiInterface` / `appliance.updateFlake`.
The original Pi Zero W is ARMv6, so it also needs compatible Codex packaging;
the Zero 2 W is ARM64. Neither is a supported target yet.

## Maintenance

From the admin account with `sudo`: `journalctl -u codex-provision -u radxa-bluetooth`,
`nmcli device`, `tailscale status`, or `systemctl start nixos-upgrade`.
Codex sign-in can also run locally with `runuser -u codex -- codex login --device-auth`.
The provisioning token is in `/var/lib/codex-provision/config.json` (root only).
Wi-Fi profiles, hostname, Codex credentials and Tailscale identity survive updates.

Hardware support follows [icefirex's ZERO 3W NixOS example](https://github.com/icefirex/nixos-radxa-zero3w),
[ritiek's RK3566 configuration](https://github.com/ritiek/dotfiles/tree/master/machines/radrubble),
and [Radxa's AIC8800 driver](https://github.com/radxa-pkg/aic8800).
Linux 6.12 is pinned for the vendor driver. Build/evaluation and mocked
provisioning checks cannot establish radio reliability: validate SD boot, AP,
BLE pairing, Wi-Fi reconnection, both login flows, and reboot persistence on the
actual board before promoting a release. Other Wi-Fi chip revisions need a
different board driver module.
