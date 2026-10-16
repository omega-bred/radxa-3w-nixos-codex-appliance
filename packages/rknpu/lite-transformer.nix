{ lib, stdenv, autoPatchelfHook, makeWrapper, libdrm
, modelZooSource, toolkitSource, encoderModel, decoderModel }:
let
  zoo = modelZooSource;
  demo = zoo + "/examples/lite_transformer";
  runtime = toolkitSource + "/rknpu2/runtime/Linux/librknn_api";
in stdenv.mkDerivation {
  pname = "rknpu-lite-transformer";
  version = "837db8e";
  dontUnpack = true;
  nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib libdrm ];
  buildPhase = ''
    mkdir -p $out/bin $out/lib $out/share/rknpu-lite-transformer/model
    cp ${runtime}/aarch64/librknnrt.so $out/lib/
    $CC -O2 -I${zoo}/utils -c ${zoo}/utils/file_utils.c -o file_utils.o
    $CXX -O2 -std=c++17 \
      -I${runtime}/include -I${zoo}/utils -I${demo}/cpp -I${zoo}/3rdparty/timer \
      -I${demo}/cpp/utils -I${demo}/cpp/utils/bpe_tools \
      -I${demo}/cpp/rknpu2/rkdemo_utils \
      ${demo}/cpp/main.cc ${demo}/cpp/rknpu2/lite_transformer.cc \
      ${demo}/cpp/rknpu2/rkdemo_utils/rknn_demo_utils.cc \
      ${demo}/cpp/utils/bpe_tools/bpe_tools.cc file_utils.o \
      -L$out/lib -Wl,-rpath,$out/lib -lrknnrt -lpthread -ldl \
      -o $out/bin/rknpu-lite-transformer
    cp ${demo}/model/{bpe_order.txt,dict_order.txt,cw_token_map_order.txt,position_embed.bin,token_embed.bin} \
      $out/share/rknpu-lite-transformer/model/
    cp ${encoderModel} $out/share/rknpu-lite-transformer/model/encoder.rknn
    cp ${decoderModel} $out/share/rknpu-lite-transformer/model/decoder.rknn
    wrapProgram $out/bin/rknpu-lite-transformer \
      --chdir $out/share/rknpu-lite-transformer \
      --add-flags "$out/share/rknpu-lite-transformer/model/encoder.rknn $out/share/rknpu-lite-transformer/model/decoder.rknn"
  '';
  installPhase = "true";
  meta = {
    description = "RK3566 NPU-assisted English-to-Chinese Lite Transformer demo";
    homepage = "https://github.com/airockchip/rknn_model_zoo/tree/main/examples/lite_transformer";
    license = [ lib.licenses.asl20 lib.licenses.unfree ];
    sourceProvenance = [ lib.sourceTypes.fromSource lib.sourceTypes.binaryNativeCode ];
    platforms = [ "aarch64-linux" ];
  };
}
