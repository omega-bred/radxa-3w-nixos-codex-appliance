#include "rknn_matmul_api.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t b_index(int k, int n, int K, int N, int native, int integer) {
    if (!native) return (size_t)k * N + n;
    int kt = integer ? 32 : 16, nt = integer ? 16 : 8;
    return (((size_t)(n / nt) * (K / kt) + k / kt) * nt + n % nt) * kt + k % kt;
}

static int test(int M, int K, int N, int native, int integer) {
    rknn_matmul_info info = {0};
    info.M = M; info.K = K; info.N = N;
    info.type = integer ? RKNN_INT8_MM_INT8_TO_INT32 : RKNN_FLOAT16_MM_FLOAT16_TO_FLOAT32;
    info.B_layout = native ? RKNN_MM_LAYOUT_NATIVE : RKNN_MM_LAYOUT_NORM;
    info.AC_layout = RKNN_MM_LAYOUT_NORM;
    rknn_matmul_io_attr attr = {0};
    rknn_matmul_ctx ctx = 0;
    rknn_tensor_mem *a = NULL, *b = NULL, *c = NULL;
    int ret = rknn_matmul_create(&ctx, &info, &attr), passed = 0;
    printf("%s M=%d K=%d N=%d B=%s create=%d\n", integer ? "INT8" : "FP16", M, K, N, native ? "native" : "normal", ret);
    if (ret) goto out;
    a = rknn_create_mem(ctx, attr.A.size);
    b = rknn_create_mem(ctx, attr.B.size);
    c = rknn_create_mem(ctx, attr.C.size);
    if (!a || !b || !c) { fprintf(stderr, "allocation failed\n"); goto out; }
    memset(a->virt_addr, 0, a->size);
    memset(b->virt_addr, 0, b->size);
    memset(c->virt_addr, 0, c->size);
    for (int m = 0; m < M; m++) for (int k = 0; k < K; k++) {
        int value = (m * 3 + k * 5) % 7 - 3;
        if (integer) ((int8_t *)a->virt_addr)[(size_t)m * K + k] = value;
        else ((_Float16 *)a->virt_addr)[(size_t)m * K + k] = (_Float16)(value / 8.0f);
    }
    for (int k = 0; k < K; k++) for (int n = 0; n < N; n++) {
        int value = (k * 2 + n * 3) % 9 - 4;
        size_t pos = b_index(k, n, K, N, native, integer);
        if (integer) ((int8_t *)b->virt_addr)[pos] = value;
        else ((_Float16 *)b->virt_addr)[pos] = (_Float16)(value / 8.0f);
    }
#define CHECK(call) do { ret = (call); if (ret) { fprintf(stderr, "%s: %d\n", #call, ret); goto out; } } while (0)
    CHECK(rknn_matmul_set_io_mem(ctx, a, &attr.A));
    CHECK(rknn_matmul_set_io_mem(ctx, b, &attr.B));
    CHECK(rknn_matmul_set_io_mem(ctx, c, &attr.C));
    CHECK(rknn_mem_sync(ctx, a, RKNN_MEMORY_SYNC_TO_DEVICE));
    CHECK(rknn_mem_sync(ctx, b, RKNN_MEMORY_SYNC_TO_DEVICE));
    CHECK(rknn_matmul_run(ctx));
    CHECK(rknn_mem_sync(ctx, c, RKNN_MEMORY_SYNC_FROM_DEVICE));
    double max_error = 0;
    for (int m = 0; m < M; m++) for (int n = 0; n < N; n++) {
        int expected = 0;
        for (int k = 0; k < K; k++) expected += ((m * 3 + k * 5) % 7 - 3) * ((k * 2 + n * 3) % 9 - 4);
        double target = integer ? expected : expected / 64.0;
        double actual = integer ? ((int32_t *)c->virt_addr)[(size_t)m * N + n] : ((float *)c->virt_addr)[(size_t)m * N + n];
        double error = fabs(actual - target);
        if (!isfinite(actual) || error > (integer ? 0 : 0.001 * (1 + fabs(target)))) {
            fprintf(stderr, "mismatch [%d,%d]: actual=%g expected=%g\n", m, n, actual, target);
            goto out;
        }
        if (error > max_error) max_error = error;
    }
    printf("PASS max_error=%.9g\n", max_error);
    passed = 1;
out:
    if (c) rknn_destroy_mem(ctx, c);
    if (b) rknn_destroy_mem(ctx, b);
    if (a) rknn_destroy_mem(ctx, a);
    if (ctx) rknn_matmul_destroy(ctx);
    return passed;
}

int main(void) {
    setbuf(stdout, NULL);
    for (int integer = 0; integer <= 1; integer++)
        for (int native = 0; native <= 1; native++)
            if (!test(1, 32, 16, native, integer) ||
                !test(4, 64, 32, native, integer) ||
                !test(1, 1024, 1024, native, integer)) return 1;
    return 0;
}
