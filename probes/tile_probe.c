// tile_probe.c - generate Tile IR via NVRTC, wrap it with nvFatbinAddTileIR,
// and write the fatbin so its entry `kind` can be read back.
#include <nvrtc.h>
#include <nvFatbin.h>
#include <stdio.h>
#include <stdlib.h>

static const char *SRC =
    "extern \"C\" __global__ void probe(int *out){ out[threadIdx.x] = 0xAAAA; }\n";

int main(void)
{
    nvrtcProgram prog;
    nvrtcResult nr = nvrtcCreateProgram(&prog, SRC, "probe.cu", 0, NULL, NULL);
    printf("nvrtcCreateProgram      -> %d\n", (int)nr);

    const char *opts[] = { "--gpu-architecture=compute_89", "--enable-tile" };
    nr = nvrtcCompileProgram(prog, 2, opts);
    printf("nvrtcCompileProgram     -> %d (%s)\n", (int)nr, nvrtcGetErrorString(nr));
    if (nr != NVRTC_SUCCESS) {
        size_t ls; nvrtcGetProgramLogSize(prog, &ls);
        char *log = malloc(ls); nvrtcGetProgramLog(prog, log);
        printf("--- log ---\n%s\n", log);
        return 1;
    }

    size_t tsz = 0;
    nr = nvrtcGetTileIRSize(prog, &tsz);
    printf("nvrtcGetTileIRSize      -> %d, size=%zu\n", (int)nr, tsz);
    if (nr != NVRTC_SUCCESS || tsz == 0) return 1;

    char *tile = malloc(tsz);
    nr = nvrtcGetTileIR(prog, tile);
    printf("nvrtcGetTileIR          -> %d\n", (int)nr);

    nvFatbinHandle h;
    const char *fopts[] = { "-cuda" };
    nvFatbinResult fr = nvFatbinCreate(&h, fopts, 1);
    printf("nvFatbinCreate          -> %d\n", (int)fr);
    fr = nvFatbinAddTileIR(h, tile, tsz, "probe_tile", "");
    printf("nvFatbinAddTileIR       -> %d\n", (int)fr);
    if (fr != NVFATBIN_SUCCESS) return 1;

    size_t fsz; nvFatbinSize(h, &fsz);
    void *buf = malloc(fsz); nvFatbinGet(h, buf);
    FILE *f = fopen("out/kind_tile.fatbin", "wb"); fwrite(buf, 1, fsz, f); fclose(f);
    printf("wrote out/kind_tile.fatbin (%zu bytes)\n", fsz);
    return 0;
}
