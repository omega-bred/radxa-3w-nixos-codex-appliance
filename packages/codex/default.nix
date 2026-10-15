{ lib, stdenvNoCC, fetchurl }:

stdenvNoCC.mkDerivation rec {
  pname = "codex";
  version = "0.163.0";

  src = fetchurl {
    url = "https://github.com/openai/codex/releases/download/rust-v${version}/codex-package-aarch64-unknown-linux-musl.tar.gz";
    hash = "sha256-nY7PLRnT4YSHeYOQLWyF3kiziI3LcsOCqKDJdYRZJ/s=";
  };

  sourceRoot = ".";
  dontConfigure = true;
  dontBuild = true;
  # Keep the upstream package layout so the CLI can find its companion tools.
  dontFixup = true;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -r bin codex-path codex-resources codex-package.json "$out/"
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    radxa_codex_test_home="$TMPDIR/codex-home"
    mkdir -p "$radxa_codex_test_home"
    env CODEX_HOME="$radxa_codex_test_home" "$out/bin/codex" --version | grep -F "codex-cli ${version}"
    env CODEX_HOME="$radxa_codex_test_home" "$out/bin/codex" remote-control --help > remote-control-help
    grep -F 'Start the app-server daemon' remote-control-help
    runHook postInstallCheck
  '';

  meta = {
    description = "OpenAI Codex CLI with its upstream companion tools";
    homepage = "https://developers.openai.com/codex/cli";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-linux" ];
    mainProgram = "codex";
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
