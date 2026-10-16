{ lib, fetchurl }:
fetchurl {
  name = "Qwen3-0.6B-Q8_0.gguf";
  url = "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf";
  sha256 = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031";
  meta = {
    description = "Official Qwen3-0.6B instruction model, Q8_0 GGUF";
    homepage = "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF";
    license = lib.licenses.asl20;
  };
}
