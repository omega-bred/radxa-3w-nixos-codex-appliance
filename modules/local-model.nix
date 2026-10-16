{ config, lib, pkgs, ... }:
let
  cfg = config.appliance.localModel;
in {
  imports = [ ../boards/radxa-zero3w/npu.nix ];
  options.appliance.localModel = {
    enable = lib.mkEnableOption "the experimental local Qwen3.5-0.8B NPU server";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../packages/rknpu/llama.nix { };
      description = "RK3566 llama.cpp runtime.";
    };
    model = lib.mkOption {
      type = lib.types.path;
      default = pkgs.callPackage ../packages/rknpu/qwen35-model.nix { };
      description = "Qwen3.5-0.8B GGUF weights.";
    };
    contextSize = lib.mkOption {
      type = lib.types.ints.between 256 2048;
      default = 512;
      description = "Maximum local model context, including prompt and answer.";
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 8081;
      description = "Loopback HTTP port for local inference.";
    };
  };
  config = lib.mkIf cfg.enable {
    appliance.npu.enable = lib.mkDefault true;
    appliance.npu.memoryMiB = lib.mkDefault 1536;
    assertions = [ {
      assertion = config.appliance.npu.enable && config.appliance.npu.autoload;
      message = "The local Qwen server requires the NPU driver to load automatically.";
    } ];
    environment.systemPackages = [
      (pkgs.writeShellApplication {
        name = "qwen-local";
        runtimeInputs = [ pkgs.curl pkgs.jq pkgs.gnused ];
        text = ''
          if [ "$#" -eq 0 ]; then
            echo 'Usage: qwen-local "Your question"' >&2
            exit 2
          fi
          qwen_request=$(jq -n --arg prompt "$*" '{
            model: "qwen-local",
            messages: [{role: "user", content: $prompt}],
            temperature: 0, max_tokens: 96, stream: true,
            chat_template_kwargs: {enable_thinking: false}
          }')
          curl --fail-with-body --silent --show-error --no-buffer --max-time 300 \
            --header 'Content-Type: application/json' --data "$qwen_request" \
            http://127.0.0.1:${toString cfg.port}/v1/chat/completions \
            | sed -u -n '/^data: \[DONE\]$/d; s/^data: //p' \
            | jq --unbuffered -jr '.choices[0].delta.content // empty'
          printf '\n'
        '';
      })
      (pkgs.writeShellScriptBin "qwen-chat" ''
        exec ${cfg.package}/bin/llama-cli \
          --server-base http://127.0.0.1:${toString cfg.port} --simple-io "$@"
      '')
    ];
    systemd.services.qwen-local = {
      description = "Local Qwen3.5-0.8B inference on the RK3566 NPU";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-modules-load.service" "rknpu-clock-limit.service" "radxa-bluetooth.service" ];
      requires = [ "rknpu-clock-limit.service" ];
      startLimitIntervalSec = 300;
      startLimitBurst = 2;
      environment = {
        OMP_NUM_THREADS = "2";
        RKNPU_HYBRID = "W16A16_STANDARD";
      };
      serviceConfig = {
        ExecStart = lib.escapeShellArgs [
          "${cfg.package}/bin/llama-server"
          "--model" (toString cfg.model) "--alias" "qwen-local"
          "--host" "127.0.0.1" "--port" (toString cfg.port)
          # Offload the 24 transformer blocks; retain the large tied output
          # projection on the CPU within the existing contiguous-memory pool.
          "--gpu-layers" "24" "--fit" "off" "--no-kv-offload"
          "--ctx-size" (toString cfg.contextSize) "--batch-size" "32" "--ubatch-size" "32"
          "--threads" "2" "--threads-batch" "2" "--parallel" "1"
          "--predict" "96"
          "--cache-ram" "0" "--ctx-checkpoints" "0" "--no-warmup" "--no-webui"
          "--chat-template-kwargs" ''{"enable_thinking":false}''
        ];
        DynamicUser = true;
        SupplementaryGroups = [ "video" "render" ];
        RuntimeDirectory = "qwen-local";
        WorkingDirectory = "/run/qwen-local";
        LimitNOFILE = 65536;
        Nice = 10;
        Restart = "on-failure";
        RestartSec = 5;
        MemoryAccounting = true;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
      };
    };
  };
}
