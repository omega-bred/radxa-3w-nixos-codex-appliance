{ lib, stdenv, fetchurl, autoPatchelfHook, makeWrapper, libdrm, python3 }:
let
  revision = "42aa1d426c0a9e0869b6374edba009f7208a1926"; # RKNN Toolkit2 v2.3.2
  upstream = path: sha256: fetchurl {
    url = "https://raw.githubusercontent.com/airockchip/rknn-toolkit2/${revision}/rknpu2/${path}";
    inherit sha256;
  };
  runtime = upstream "runtime/Linux/librknn_api/aarch64/librknnrt.so"
    "d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8";
  header = upstream "runtime/Linux/librknn_api/include/rknn_api.h"
    "c48e11a6f41b451a5fd1e4ad774ea60252d3d94f78bee9b21ea3d21b21deba9a";
  model = upstream "examples/rknn_mobilenet_demo/model/RK3566_RK3568/mobilenet_v1.rknn"
    "92b36515616fc568ddc573f07a345cf28441efe0d24ecc9b98051fe8bcfad980";
  dog = upstream "examples/rknn_mobilenet_demo/model/dog_224x224.jpg"
    "c350299c6283d5f62fecf1f845b6b3be9aafec8dff528ca09a129990f0a584b0";
in stdenv.mkDerivation {
  pname = "rknpu-smoke";
  version = "2.3.2";
  dontUnpack = true;
  nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib libdrm ];
  buildPhase = ''
    mkdir -p include $out/bin $out/lib $out/share/rknpu-smoke
    cp ${header} include/rknn_api.h
    cp ${runtime} $out/lib/librknnrt.so
    $CC -O2 -Wall -Wextra ${./smoke.c} -Iinclude \
      -L$out/lib -Wl,-rpath,$out/lib -lrknnrt -lm -o $out/bin/rknpu-smoke
    cp ${model} $out/share/rknpu-smoke/mobilenet_v1.rknn
    ${python3.withPackages (p: [ p.pillow ])}/bin/python - <<'PY'
from PIL import Image
from pathlib import Path
import os
image = Image.open('${dog}').convert('RGB')
assert image.size == (224, 224)
Path(os.environ['out'], 'share/rknpu-smoke/dog.rgb').write_bytes(image.tobytes())
PY
    wrapProgram $out/bin/rknpu-smoke --add-flags \
      "$out/share/rknpu-smoke/mobilenet_v1.rknn $out/share/rknpu-smoke/dog.rgb"
  '';
  installPhase = "true";
  meta = {
    description = "Known-input RK3566 NPU inference check using Rockchip's MobileNet sample";
    homepage = "https://github.com/airockchip/rknn-toolkit2";
    license = lib.licenses.unfree;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
    platforms = [ "aarch64-linux" ];
  };
}
