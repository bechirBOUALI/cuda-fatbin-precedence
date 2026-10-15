// Feed REAL Tile IR bytecode (captured from cutile-rs invoking tileiras) to
// nvFatbinAddTileIR, which previous probes only fed synthetic blobs.
#include <nvFatbin.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("open"); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *code = malloc(n);
    if (fread(code, 1, n, f) != (size_t)n) { perror("read"); return 1; }
    fclose(f);
    printf("input %s, %ld bytes, first4 %02x %02x %02x %02x\n", argv[1], n,
           ((unsigned char*)code)[0], ((unsigned char*)code)[1],
           ((unsigned char*)code)[2], ((unsigned char*)code)[3]);

    nvFatbinHandle h = NULL;
    const char *opts[] = { "-cuda" };
    nvFatbinResult r = nvFatbinCreate(&h, opts, 1);
    printf("nvFatbinCreate     -> %d\n", (int)r);
    if (r != NVFATBIN_SUCCESS) return 1;

    r = nvFatbinAddTileIR(h, code, n, "real_tile", argc > 2 ? argv[2] : "");
    printf("nvFatbinAddTileIR  -> %d (%s)\n", (int)r, nvFatbinGetErrorString(r));
    if (r != NVFATBIN_SUCCESS) { nvFatbinDestroy(&h); return 1; }

    size_t sz = 0;
    r = nvFatbinSize(h, &sz);
    printf("nvFatbinSize       -> %d, %zu bytes\n", (int)r, sz);
    unsigned char *buf = malloc(sz);
    r = nvFatbinGet(h, buf);
    printf("nvFatbinGet        -> %d\n", (int)r);
    if (r == NVFATBIN_SUCCESS) {
        FILE *o = fopen("real_tile.fatbin", "wb");
        fwrite(buf, 1, sz, o); fclose(o);
        unsigned short hs = buf[6] | (buf[7] << 8);
        unsigned short kind = buf[hs] | (buf[hs+1] << 8);
        printf("header_size 0x%x, FIRST ENTRY KIND = 0x%x\n", hs, kind);
    }
    nvFatbinDestroy(&h);
    return 0;
}
