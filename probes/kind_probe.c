// kind_probe.c - build one fatbin per nvFatbin Add* function, so the resulting
// entry "kind" field can be read back and mapped to the API that produced it.
#include <nvFatbin.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(expr) do { nvFatbinResult _r = (expr);                       \
    if (_r != NVFATBIN_SUCCESS) {                                          \
        fprintf(stderr, "  %-10s FAILED: %s -> result %d\n", tag, #expr, (int)_r); \
        return -1; }                                                       \
    } while (0)

static void *slurp(const char *p, size_t *n, int add_nul)
{
    FILE *f = fopen(p, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", p); exit(1); }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *b = malloc(sz + 1);
    if (fread(b, 1, sz, f) != (size_t)sz) { exit(1); }
    fclose(f);
    b[sz] = 0;
    *n = add_nul ? (size_t)sz + 1 : (size_t)sz;
    return b;
}

static int emit(const char *tag, nvFatbinHandle h, const char *out)
{
    size_t sz = 0;
    CHECK(nvFatbinSize(h, &sz));
    void *buf = malloc(sz);
    CHECK(nvFatbinGet(h, buf));
    FILE *f = fopen(out, "wb"); fwrite(buf, 1, sz, f); fclose(f);
    printf("  %-10s -> %s (%zu bytes)\n", tag, out, sz);
    free(buf);
    return 0;
}

int main(void)
{
    const char *opts[] = { "-cuda" };
    size_t n;
    nvFatbinHandle h;
    const char *tag;

    // ---- PTX
    tag = "AddPTX";
    { void *d = slurp("../build/variant_a.ptx", &n, 1);
      CHECK(nvFatbinCreate(&h, opts, 1));
      CHECK(nvFatbinAddPTX(h, d, n, "89", "probe_ptx", ""));
      if (emit(tag, h, "out/kind_ptx.fatbin")) return 1;
      CHECK(nvFatbinDestroy(&h)); }

    // ---- Cubin
    tag = "AddCubin";
    { void *d = slurp("../build/variant_a.cubin", &n, 0);
      CHECK(nvFatbinCreate(&h, opts, 1));
      CHECK(nvFatbinAddCubin(h, d, n, "89", "probe_cubin"));
      if (emit(tag, h, "out/kind_cubin.fatbin")) return 1;
      CHECK(nvFatbinDestroy(&h)); }

    // ---- Reloc (relocatable PTX taken from an -rdc=true host object)
    tag = "AddReloc";
    { void *d = slurp("../build/rdc.o", &n, 0);
      CHECK(nvFatbinCreate(&h, opts, 1));
      CHECK(nvFatbinAddReloc(h, d, n));
      if (emit(tag, h, "out/kind_reloc.fatbin")) return 1;
      CHECK(nvFatbinDestroy(&h)); }

    return 0;
}
