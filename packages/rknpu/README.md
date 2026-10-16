# Experimental ZERO 3W NPU support

`boards/radxa-zero3w/npu.nix` adds the vendor-compatible RKNPU interface to the
appliance's existing mainline Linux 6.12 kernel. The standard flake enables
it through `modules/local-model.nix` for Qwen3-0.6B on the tested 4 GB board.
The module can also be used independently for smaller RKNN workloads.

Add the module to a NixOS configuration and set:

```nix
imports = [ ./boards/radxa-zero3w/npu.nix ];
appliance.npu.enable = true;
# Keep the driver unloaded for the first boot; load it manually after SSH works.
appliance.npu.autoload = false;
```

The module defaults to 128 MiB of contiguous DMA memory, configurable through
`appliance.npu.memoryMiB`. It uses the ZERO 3W's
`vdd_gpu_npu` supply and the native CRU clock, because the appliance's TF-A does
not provide SCMI clocks. Available operating points are 200–400 MHz at 900 mV;
`appliance.npu.maxFrequencyMHz` defaults to a 200 MHz ceiling. It does not
enable the GPU or the NPU IOMMU. The latter is untested on this board with the
mainline kernel; the driver port supports contiguous DMA instead.

The GPL driver is pinned to `w568w/rknpu-module` commit
`a8792fe6b633f90cf2c6808267cac327537ab4cf`. Its mainline adaptation was tested by
its author on a different RK3566 board with newer kernels. This package adapts
the timer, page-fault, DMA namespace and devfreq governor interfaces for 6.12.
Successful builds and device enumeration do not establish working inference.
The connected ZERO 3W has now passed actual inference, as recorded below.

For hardware validation, run a known RK3566 RKNN model using a matching RKNN
runtime. Check the expected output, increasing NPU interrupt counts, kernel
errors, temperature, and network/Bluetooth access. Keep the known-good NixOS
generation available until the hardware trial succeeds.

Radxa reports that some ZERO 3W RK3566-T chips have an unqualified NPU. Neither
the generic device-tree model nor its configured CPU frequency proves the chip
bin or a working NPU. An inference test establishes whether the tested hardware
path works.

This driver exposes RKNN inference. The separate experimental llama.cpp port
below uses the RKNN matrix multiplication API; Rockchip's RKLLM runtime still
does not support this chip.

## Hardware test, 2026-10-15

The trial uses the stock-frequency thermal fix from appliance commit `015a249`,
Linux 6.12.93, RKNPU 0.9.8, and RKNN runtime 2.3.2. CPU thermal trip points and
the disabled 1.8 GHz CPU OPP were preserved in the compiled device tree.

The preconverted RK3566/RK3568 MobileNet V1 model correctly identified the
published dog image as class 156 (Shih-Tzu), with score `0.935059`.
The first `rknn_run` took 16.636 ms; three repeat runs took 15.401, 15.435,
and 15.512 ms. The NPU interrupt count increased once per inference, from
zero to four. This proves hardware execution for this workload, without
establishing the chip's `-T` bin or general model compatibility.

The NPU was at 200 MHz and 900 mV. The final sampled SoC temperature was
53.75 C, with provisioning, Bluetooth, and Tailscale services active and no
NPU kernel errors. These were short functional tests, not a sustained-load
qualification. The non-IOMMU driver needs contiguous DMA memory.

`smoke.nix` packages that exact model, input image, runtime and a small C test
with pinned download hashes. Build it on aarch64 Linux using the repository's
pinned nixpkgs and `pkgs.callPackage ./packages/rknpu/smoke.nix { }`. The
proprietary Rockchip runtime requires allowing the `rknpu-smoke` package in
nixpkgs' unfree predicate. Run the resulting `bin/rknpu-smoke` with `sudo`;
it returns nonzero for an unexpected class or an inference error. The vendor
runtime opens a DRM card device. The local model service receives video/render
group access; standalone tests need equivalent access or `sudo`.

## Already ported language models

The model must target RK3566; RK3588 `.rkllm` files are not interchangeable.
The first verified language-model candidate was Rockchip's
[Lite Transformer](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/lite_transformer).
Its conversion script explicitly accepts RK3566, and its Linux C++ demo includes
tokenization, embeddings, encoder and autoregressive decoder. It translates
short English sentences to Chinese, with a 16-token sequence configuration;
it cannot answer general troubleshooting questions.

The connected ZERO 3W passed the published `thank you` example, producing
`感谢你` in 132.226 ms (encoder 8.645 ms, four decoder iterations).
The NPU interrupt count increased from 5 to 106 during that invocation, with
no NPU kernel errors. Tokenization, embedding lookup and decoding include CPU
work; this is NPU-assisted translation.

The tested sources were RKNN model zoo commit
`837db8e992815ff8647eadb709f07067f4bc3a71` and RKNN Toolkit2 v2.3.2 commit
`42aa1d426c0a9e0869b6374edba009f7208a1926`. The upstream
`examples/lite_transformer/python/convert.py` was run for both published ONNX
files with `rk3566 fp` as its target and precision arguments, on an off-board
aarch64 Linux container. Conversion required `setuptools<81` for the toolkit's
`pkg_resources` import. `lite-transformer.nix` packages the upstream C++ demo;
provide `modelZooSource`, `toolkitSource`, `encoderModel`, and `decoderModel`
to `pkgs.callPackage`, allowing the `rknpu-lite-transformer` package in the
unfree predicate. It produces the exact tested store output.

After a second reboot, the driver loaded automatically and both installed
tests passed again. Translation took 134.668 ms; NPU interrupts increased
from zero to 102 across MobileNet and translation. Wi-Fi SSH access,
Bluetooth and provisioning were active, the CPU maximum remained 1.608 GHz,
and the final SoC temperature sample was 61.666 C.

On the board, the tests are retained in separate Nix profiles to avoid the
two packages' private runtime library paths conflicting:

```sh
sudo /nix/var/nix/profiles/radxa-npu-smoke/bin/rknpu-smoke
sudo /nix/var/nix/profiles/radxa-npu-transformer/bin/rknpu-lite-transformer thank you
```

These tests preceded the Qwen trial below. During the unpublished trial,
`nixos-upgrade.timer` was masked to preserve the experimental configuration.
After promoting the enabled NPU/Qwen flake to stable and verifying the board's
matching system, restore updates with `sudo systemctl unmask nixos-upgrade.timer`
and `sudo systemctl start nixos-upgrade.timer`.
The saved normal system is
`/nix/store/3kyl02n1d2jhk3llllsxr5fl16pz35k8-nixos-system-codex-sd-card-25.11.20260630.b6018f8`.

The off-board test workspace retains conversion commands, SHA-256 hashes,
the Python dependency list, builds and live logs under `work/npu/` (ignored
by Git). `work/npu/after-reboot.log` records the final boot and inference
checks. The converted models are under `work/npu/lite-transformer/`.

Upstream chat-model support was insufficient for this board:

- [RKLLM](https://github.com/airockchip/rknn-llm#support-platform) supports
  RK3588, RK3576, RK3562 and RV1126B, excluding RK3566.
- [rk-llama.cpp](https://github.com/invisiofficial/rk-llama.cpp/tree/rknpu2/ggml/src/ggml-rknpu2)
  offers an NPU backend for GGUF models, but does not provide a tested RK3566
  configuration. It is the starting point for the port below.
- [SmolVLM2-256M-NPU](https://github.com/Qengineering/SmolVLM2-256M-NPU)
  explicitly targets RK3588; its small parameter count does not make it a
  ready RK3566 model.

## Experimental Qwen3-0.6B CLI

`modules/local-model.nix` packages a local HTTP server and two CLI clients:

```nix
imports = [ ./modules/local-model.nix ];
appliance.localModel.enable = true;
# Defaults: 1536 MiB CMA, NPU ceiling 200 MHz, 512-token context,
# FP16 matrix operations, single inference slot, loopback port 8081.
```

The repository's standard `radxa-zero3w` configuration already imports and
enables this module. A custom configuration can disable it with
`appliance.localModel.enable = lib.mkForce false;`; the NPU driver and large
CMA pool then remain disabled unless enabled separately. Use that configuration
for smaller RAM variants or boards whose NPU has not passed hardware testing.

On the board, after the service is ready:

```sh
qwen-local "Give me two commands to inspect Linux memory usage."
qwen-chat
# Interactive commands: /clear, /read <file>, /exit
systemctl status qwen-local
journalctl -u qwen-local -f
```

Both commands use the running service; they do not load a second model. The
server listens on `127.0.0.1:8081`, accepts OpenAI-compatible chat requests,
disables thinking by default, and defaults to at most 96 generated tokens.
`qwen-local` streams a single answer with a five-minute timeout. `qwen-chat`
retains conversation history until `/clear` or exit. The 512-token context
includes the question, history and answer, so keep questions and logs short.
Initial model loading takes tens of seconds. The model does not automatically
read system state, execute commands, or repair the appliance.

The model is the official Qwen3-0.6B Q8_0 GGUF, pinned in `qwen-model.nix` to
Hugging Face revision `23749fefcc72300e3a2ad315e1317431b06b590a` with SHA-256
`9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031`.
This port does not create an `.rkllm` file: llama.cpp loads the GGUF and packs
supported weight matrices for the NPU at startup. Attention, tokenization and
unsupported operators remain on the CPU. The `--gpu-layers` option is GGML's
generic device-offload control; execution here uses the NPU.

`llama.nix` pins `invisiofficial/rk-llama.cpp` at
`e1862168d04f13a9e61826a4167e8be032eedbd9`, with `rk3566-llama.patch` adding
the single-core RK3566 pipeline, FP16 K16/N8 and INT8 K32/N16 native tiles,
and alignment checks. The patch also preserves original GGUF bytes for CPU
fallback/readback, sizes the shadow buffer for both representations, checks
allocator context creation, skips unsupported multicore selection, and enables
explicit layer offload. The bundled RKNN runtime is version 2.3.2.

The service runs as a dynamic user with video/render access, two CPU threads,
low scheduling priority, and a 65536 file descriptor limit. Full-model context
creation exceeded the normal 1024-descriptor limit in an earlier trial.
The tested configuration retains the appliance's stock CPU frequency limit
and thermal trips. CMA is a shared RAM pool for contiguous DMA allocations;
this changes available memory, not the NPU's physical compute capacity.

Validation on the connected board passed 12 native matrix tests (FP16/INT8,
normal/native B layouts, including K1024/N1024) and 7/7 GGML matrix comparisons
in each precision. Full-model generation completed with both FP16 and INT8.
For the same short Wi-Fi question, FP16 measured 4.8 prompt tokens/sec and
0.8 generated tokens/sec; INT8 measured 9.1 and 1.3 respectively. INT8 changed
one answer from `ip a` to the incorrect Linux command `ipconfig`, so the
installed service retains FP16. A separate short CPU-only `READY` trial
measured 6.4 generated tokens/sec; these are functional samples, not a
controlled throughput benchmark. The installed `qwen-local` memory question
returned `free -m`, with interrupts increasing from 7722 to 8510. Both clients
passed against the shared service.
After reboot, the service started automatically and `qwen-local` returned
`READY`, with NPU interrupts increasing from 197 to 591 and zero service
restarts. The NPU ceiling remained 200 MHz and CMA remained 1536 MiB.
Provisioning, Bluetooth, NetworkManager and Tailscale were active; BlueZ
reported a powered adapter with one active advertisement. CPU maximum
remained 1.608 GHz, the final temperature sample was 67.5 C, and no NPU
errors or OOM kills appeared in the boot's kernel log. These checks are in
`work/npu/qwen-after-reboot.log` and `qwen-after-reboot-health.log`.
This is an experimental port with short functional tests, not a sustained-load
qualification or a troubleshooting-quality evaluation. Tiny-model answers can
be wrong, and NPU execution is currently slower than the CPU-only baseline.
Keep one NPU inference process active at a time; use these clients against the
service instead of starting standalone hardware tests while it is running.

For standalone hardware checks, build `.#rknpu-matmul-smoke` or `.#rknpu-smoke`
on an aarch64 Linux builder. Stop `qwen-local.service` before running their
binaries with `sudo`, and restart it afterward. `.#qwen-runtime` exposes the
same pinned runtime used by the service, including `test-backend-ops`.

Build and board logs are retained under ignored `work/npu/`, including
`matmul-results.log`, `backend-fp16-final.log`, `backend-int8-final.log`, and
the `qwen-*.log` inference results.

Sources:

- <https://github.com/w568w/rknpu-module>
- <https://forum.radxa.com/t/zero-3w-camera-fpc-22-pin-npu/19720/4>
- <https://github.com/airockchip/rknn-llm#support-platform>
