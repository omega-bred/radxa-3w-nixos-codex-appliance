#include "rknn_api.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static void *read_file(const char *path, size_t *size) {
    FILE *file = fopen(path, "rb");
    if (!file || fseek(file, 0, SEEK_END)) return NULL;
    long length = ftell(file);
    if (length <= 0 || length > 64 * 1024 * 1024 || fseek(file, 0, SEEK_SET)) {
        fclose(file);
        return NULL;
    }
    void *data = malloc((size_t)length);
    if (!data || fread(data, 1, (size_t)length, file) != (size_t)length) {
        free(data);
        fclose(file);
        return NULL;
    }
    fclose(file);
    *size = (size_t)length;
    return data;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s model.rknn input.rgb\n", argv[0]);
        return 2;
    }
    size_t model_size = 0, input_size = 0;
    void *model = read_file(argv[1], &model_size);
    void *pixels = read_file(argv[2], &input_size);
    if (!model || !pixels || input_size != 224 * 224 * 3) return 2;
    rknn_context ctx = 0;
    int ret = rknn_init(&ctx, model, (uint32_t)model_size, 0, NULL);
    if (ret) { fprintf(stderr, "rknn_init: %d\n", ret); return 1; }
    rknn_sdk_version version = {0};
    ret = rknn_query(ctx, RKNN_QUERY_SDK_VERSION, &version, sizeof(version));
    if (ret) return 1;
    printf("runtime=%s driver=%s\n", version.api_version, version.drv_version);
    rknn_input_output_num io = {0};
    ret = rknn_query(ctx, RKNN_QUERY_IN_OUT_NUM, &io, sizeof(io));
    if (ret || io.n_input != 1 || io.n_output != 1) return 1;
    rknn_input input = {
        .index = 0, .buf = pixels, .size = (uint32_t)input_size,
        .type = RKNN_TENSOR_UINT8, .fmt = RKNN_TENSOR_NHWC,
    };
    ret = rknn_inputs_set(ctx, 1, &input);
    if (ret) { fprintf(stderr, "rknn_inputs_set: %d\n", ret); return 1; }
    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);
    ret = rknn_run(ctx, NULL);
    clock_gettime(CLOCK_MONOTONIC, &end);
    if (ret) { fprintf(stderr, "rknn_run: %d\n", ret); return 1; }
    rknn_output output = {.index = 0, .want_float = 1};
    ret = rknn_outputs_get(ctx, 1, &output, NULL);
    if (ret) { fprintf(stderr, "rknn_outputs_get: %d\n", ret); return 1; }
    const float *scores = output.buf;
    size_t count = output.size / sizeof(float);
    if (!count || count > 10000) return 1;
    size_t best = 0;
    for (size_t i = 0; i < count; i++) {
        if (!isfinite(scores[i])) return 1;
        if (scores[i] > scores[best]) best = i;
    }
    double elapsed_ms = (end.tv_sec - start.tv_sec) * 1000.0 +
                        (end.tv_nsec - start.tv_nsec) / 1000000.0;
    printf("top_class=%zu score=%.6f inference_ms=%.3f\n", best, scores[best], elapsed_ms);
    /* Rockchip's published expected result for this dog image is Shih-Tzu. */
    int passed = best == 156 && scores[best] > 0.5f;
    rknn_outputs_release(ctx, 1, &output);
    rknn_destroy(ctx);
    free(model);
    free(pixels);
    return passed ? 0 : 1;
}
