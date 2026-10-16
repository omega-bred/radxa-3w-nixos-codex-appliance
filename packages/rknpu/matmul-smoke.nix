{ lib, stdenv, autoPatchelfHook, libdrm, callPackage }:
let
  runtimeSource = (callPackage ./llama.nix { }).src;
  sdk = "${runtimeSource}/ggml/src/ggml-rknpu2/libs";
in stdenv.mkDerivation {
  pname = "rknpu-matmul-smoke";
  version = "2.3.2";
  dontUnpack = true;
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib libdrm ];
  buildPhase = ''
    mkdir -p $out/bin $out/lib
    cp ${sdk}/librknnrt.so $out/lib/
    $CC -O2 -Wall -Wextra ${./matmul-smoke.c} -I${sdk}/include \
      -L$out/lib -Wl,-rpath,$out/lib -lrknnrt -lm -o $out/bin/rknpu-matmul-smoke
  '';
  installPhase = "true";
  meta = {
    description = "RK3566 FP16 and INT8 matrix checks against CPU reference results";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.fromSource lib.sourceTypes.binaryNativeCode ];
    platforms = [ "aarch64-linux" ];
  };
}
