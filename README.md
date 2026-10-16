# Codex appliance

A headless NixOS SD-card image for the **Radxa ZERO 3W (RK3566, AIC8800)**.
Includes Codex CLI, key-only SSH over Tailscale, Wi-Fi/BLE provisioning, Tailscale/Headscale,
and daily system updates. No desktop, default SSH password, or secrets in the
flake. Use a good power supply and a 16 GB or larger microSD card.

The standard image also runs Qwen3-0.6B locally using the RK3566 NPU. This
configuration is tested on the 4 GB board and uses a 1536 MiB contiguous DMA
pool, with the NPU limited to 200 MHz. See the [NPU notes](packages/rknpu/README.md)
for limitations and hardware validation.

The board pins Codex CLI **0.163.0** from OpenAI's ARM64 Linux release, including
`codex remote-control`. Its version and download hash are in
`packages/codex/default.nix`, independent of the NixOS kernel and driver pin.

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
4. Click **Sign in to Codex**. The Codex panel shows an **Open ChatGPT sign-in**
   link and a **Copy code** button. Open the link on your phone/laptop and enter
   the code; the panel refreshes automatically until sign-in completes. Device
   authorization must be enabled for your ChatGPT account. Credentials stay in
   `/var/lib/codex/.codex`, owned by the `codex` user.
5. Click **Connect Tailscale**, then **Open device registration** when the link
   appears. For Headscale, enter your control-server URL and optional device
   name first; approve the node using that deployment's enrollment flow. The
   panel shows **Connected** and the SSH address when ready. Tailscale identity
   persists under `/var/lib/tailscale`; DNS stays with the local network.

Use **Refresh status** to check again at any time. The **Recovery token** section
lets you copy the token saved in this browser or enter one from another browser.
Raw CLI output and exit codes remain available under **Diagnostics**.

Once enrolled in Tailscale/Headscale, run `ssh codex@<tailscale-ip>`, then `codex`.
Use `ssh admin@<tailscale-ip>` for maintenance with **passwordless `sudo`**.
SSH ingress is allowed only on `tailscale0`, including IPv6; Wi-Fi and Ethernet
cannot reach it. Use a tailnet DNS name instead of the IP if your deployment
provides one. Codex runs as an unprivileged user; provisioning and updates run as
system services.

Both accounts accept the six public keys from [mindstorms6 on GitHub](https://github.com/mindstorms6.keys),
snapshotted in `modules/mindstorms6.keys`. They are baked into the image; GitHub
key changes require a repository update and release. The optional provisioning
key adds access alongside these keys.

First setup trusts nearby users: the initial AP password is public and BLE uses
encrypted Just Works pairing. Provision in a trusted place. After the first
claim, every API request needs the random token. Wi-Fi passwords go into root-only
NetworkManager profiles, and HTTP listens only on the initial hotspot address.
Wi-Fi supports open networks and WPA2 Personal (including WPA2/WPA3 mixed mode).

### BLE API

Service UUID: `d93f0001-7b2c-4e2a-9e74-41c868b36f90`.
Read/write characteristic: `d93f0002-7b2c-4e2a-9e74-41c868b36f90`.
Send UTF-8 JSON ending in a newline in 20-byte chunks, then repeat characteristic
reads until the response ends in a newline. Responses belong to the connected
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

The board uses Linux's native Rockchip CPU clock controller for frequency
scaling and thermal throttling. The bundled mainline TF-A firmware does not
provide SCMI clocks, so enabling SCMI alone is insufficient. On-board builds
use one job and one core; the updater refuses to build if CPU frequency scaling
or CPU thermal cooling is unavailable. The CPU's 70/75°C thermal trips and 95°C
emergency shutdown remain enabled. At 75°C, all four CPUs are limited to the
lowest operating point (408 MHz) until the board cools below the trip's 2°C
hysteresis.
The generic SoC's 1.8 GHz operating point is disabled to retain the ZERO 3W's
[documented 1.6 GHz rating](https://docs.radxa.com/en/zero/zero3#features)
(the 1.608 GHz operating point).

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

Run `qwen-local "your question"` for one local answer, or `qwen-chat` for an
interactive session (`/exit` to leave). Both use the same local model service;
initial loading after boot takes tens of seconds. The model currently generates
about 0.8 tokens/sec and can give incorrect answers. It does not execute repair
commands. Inspect it with `systemctl status qwen-local` or
`journalctl -u qwen-local -f`. The [NPU notes](packages/rknpu/README.md) describe
the experimental port, memory use and validation.

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
