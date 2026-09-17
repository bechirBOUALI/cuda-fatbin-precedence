// loader.cpp - fatbin entry-precedence harness.
//
// Loads a module image (fatbin, cubin, or PTX) supplied on the command line,
// looks up the kernel "probe", launches it, and reports the marker it wrote.
// Both kernel variants export "probe" and differ only in that marker, so the
// value that comes back identifies WHICH entry the driver selected.
//
//   ./loader <image-file> [--fatbinary]
//
//   --fatbinary  load via cuModuleLoadFatBinary instead of cuModuleLoadData,
//                to check whether the two entry points disagree.
//
// Run with CUDA_CACHE_DISABLE=1 so a cached JIT result cannot masquerade as
// a fresh selection decision.

#include <cuda.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

// Every driver call is checked. An unchecked failure here would print a stale
// buffer and be recorded as a precedence result, which would be worse than an
// abort: a silently wrong data point.
#define CU_CHECK(call)                                                        \
    do {                                                                      \
        CUresult _e = (call);                                                 \
        if (_e != CUDA_SUCCESS) {                                             \
            const char *_n = nullptr, *_s = nullptr;                          \
            cuGetErrorName(_e, &_n);                                          \
            cuGetErrorString(_e, &_s);                                        \
            std::fprintf(stderr, "FAIL %s:%d: %s\n  %s (%d): %s\n",           \
                         __FILE__, __LINE__, #call,                           \
                         _n ? _n : "?", (int)_e, _s ? _s : "?");              \
            std::exit(1);                                                     \
        }                                                                     \
    } while (0)

static const int  kThreads  = 4;
static const int  kSentinel = 0xDEAD;   // pre-fill; survives if nothing ran

// Read the image as raw bytes. PTX must be NUL-terminated because the driver
// treats it as a C string; cubins and fatbins are length-delimited internally,
// so the extra byte is harmless for them.
static std::vector<char> read_image(const char *path)
{
    std::FILE *f = std::fopen(path, "rb");
    if (!f) { std::fprintf(stderr, "cannot open %s\n", path); std::exit(1); }
    std::fseek(f, 0, SEEK_END);
    long n = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (n <= 0) { std::fprintf(stderr, "empty %s\n", path); std::exit(1); }

    std::vector<char> buf((size_t)n + 1, 0);
    if (std::fread(buf.data(), 1, (size_t)n, f) != (size_t)n) {
        std::fprintf(stderr, "short read on %s\n", path); std::exit(1);
    }
    std::fclose(f);
    return buf;
}

static const char *decode(int marker)
{
    switch (marker) {
        case 0xAAAA:    return "variant_a";
        case 0xBBBB:    return "variant_b";
        case kSentinel: return "NOTHING RAN (buffer untouched)";
        default:        return "unrecognised";
    }
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s <image-file> [--fatbinary]\n", argv[0]);
        return 2;
    }
    const char *path = argv[1];
    bool use_fatbin_api = (argc > 2 && std::strcmp(argv[2], "--fatbinary") == 0);

    std::vector<char> image = read_image(path);

    CU_CHECK(cuInit(0));

    int count = 0;
    CU_CHECK(cuDeviceGetCount(&count));
    if (count == 0) { std::fprintf(stderr, "no CUDA device\n"); return 1; }

    CUdevice dev;
    CU_CHECK(cuDeviceGet(&dev, 0));
    CUcontext ctx;
    // CUDA 13 uses cuCtxCreate_v4, which takes a CUctxCreateParams*.
    // The 3-argument form in most tutorials is the CUDA 12 signature.
    CU_CHECK(cuCtxCreate(&ctx, nullptr, 0, dev));

    CUmodule mod;
    if (use_fatbin_api) {
        CU_CHECK(cuModuleLoadFatBinary(&mod, image.data()));
    } else {
        CU_CHECK(cuModuleLoadData(&mod, image.data()));
    }

    CUfunction probe;
    CU_CHECK(cuModuleGetFunction(&probe, mod, "probe"));

    CUdeviceptr d_out;
    CU_CHECK(cuMemAlloc(&d_out, kThreads * sizeof(int)));

    std::vector<int> host(kThreads, kSentinel);
    CU_CHECK(cuMemcpyHtoD(d_out, host.data(), kThreads * sizeof(int)));

    // kernelParams is an array of pointers TO each argument, not the arguments
    // themselves. probe takes one int*, so this is the address of d_out.
    void *args[] = { &d_out };

    CU_CHECK(cuLaunchKernel(probe,
                            1, 1, 1,              // grid
                            kThreads, 1, 1,       // block
                            0,                    // shared memory
                            nullptr,              // stream
                            args,
                            nullptr));

    // Synchronise explicitly so a launch failure surfaces as an error rather
    // than as a stale buffer read back below.
    CU_CHECK(cuCtxSynchronize());
    CU_CHECK(cuMemcpyDtoH(host.data(), d_out, kThreads * sizeof(int)));

    std::printf("image   : %s\n", path);
    std::printf("load api: %s\n",
                use_fatbin_api ? "cuModuleLoadFatBinary" : "cuModuleLoadData");
    std::printf("marker  : 0x%04X -> %s\n", host[0], decode(host[0]));

    for (int i = 1; i < kThreads; ++i) {
        if (host[i] != host[0]) {
            std::printf("WARNING: thread %d disagrees (0x%04X)\n", i, host[i]);
        }
    }

    CU_CHECK(cuMemFree(d_out));
    CU_CHECK(cuModuleUnload(mod));
    CU_CHECK(cuCtxDestroy(ctx));
    return 0;
}
