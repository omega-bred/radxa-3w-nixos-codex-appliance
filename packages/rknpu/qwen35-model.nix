{ lib, fetchurl }:
fetchurl {
  name = "Qwen3.5-0.8B-Q8_0.gguf";
  url = "https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF/resolve/8fea620810c4afa23dd6443f999a48574c1611a3/Qwen3.5-0.8B-Q8_0.gguf";
  sha256 = "37ae482d336108d23516fa35e8e0c4126688d81018b87178a18d752a1357814f";
  meta = {
    description = "Experimental Qwen3.5-0.8B instruction model, Q8_0 GGUF";
    homepage = "https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF";
    license = lib.licenses.asl20;
  };
}
