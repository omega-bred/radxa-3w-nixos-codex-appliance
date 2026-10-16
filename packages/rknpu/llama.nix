{ lib, stdenv, fetchFromGitHub, cmake, ninja, autoPatchelfHook, makeWrapper
, libdrm, openssl }:
stdenv.mkDerivation {
  pname = "rk-llama-rk3566";
  version = "e1862168";
  src = fetchFromGitHub {
    owner = "invisiofficial";
    repo = "rk-llama.cpp";
    rev = "e1862168d04f13a9e61826a4167e8be032eedbd9";
    hash = "sha256-rG0IFc6fGMNa05vIHi2Skp9Gdg8Xm4EUlU6j6Ijt8Bc=";
  };
  patches = [ ./rk3566-llama.patch ];
  nativeBuildInputs = [ cmake ninja autoPatchelfHook makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib libdrm openssl ];
  cmakeFlags = [
    "-DLLAMA_RKNPU2=ON" "-DGGML_NATIVE=OFF"
    "-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16"
    "-DLLAMA_BUILD_TESTS=ON" "-DLLAMA_BUILD_EXAMPLES=OFF"
    "-DLLAMA_BUILD_TOOLS=ON" "-DLLAMA_BUILD_APP=OFF"
    "-DLLAMA_BUILD_UI=OFF" "-DLLAMA_BUILD_SERVER=ON"
    "-DLLAMA_USE_PREBUILT_UI=OFF"
    "-DLLAMA_BUILD_NUMBER=0" "-DLLAMA_BUILD_COMMIT=e1862168-rk3566"
  ];
  buildPhase = ''
    cmake --build . --parallel "$NIX_BUILD_CORES" --target llama-cli llama-bench llama-server test-backend-ops
  '';
  installPhase = ''
    mkdir -p $out/bin $out/lib
    cp bin/{llama-cli,llama-bench,llama-server,test-backend-ops} $out/bin/
    cp -a bin/*.so* $out/lib/
    cp ../ggml/src/ggml-rknpu2/libs/librknnrt.so $out/lib/
    # These selected targets are installed without CMake's full install tree.
    # Let autoPatchelf resolve dependencies from the installed libraries.
    for npu_elf in $out/bin/* $out/lib/*.so*; do
      patchelf --remove-rpath "$npu_elf"
    done
    for npu_tool in $out/bin/*; do
      wrapProgram "$npu_tool" --set RKNPU_DEVICE RK3566 \
        --set-default OMP_NUM_THREADS 2
    done
  '';
  meta = {
    description = "Experimental llama.cpp backend for the RK3566 NPU";
    homepage = "https://github.com/invisiofficial/rk-llama.cpp";
    license = [ lib.licenses.mit lib.licenses.unfree ];
    sourceProvenance = [ lib.sourceTypes.fromSource lib.sourceTypes.binaryNativeCode ];
    platforms = [ "aarch64-linux" ];
  };
}
