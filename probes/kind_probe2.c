// kind_probe2.c - pin the two entry kinds kind_probe.c could not reach.
//
// libnvfatbin exposes six Add* functions. Four were already mapped to kind
// values by building one fatbin each and reading the kind field back:
// PTX = 1, Cubin = 2, LTOIR = 8, Reloc = 0x40. The remaining two,
// nvFatbinAddIndex and nvFatbinAddTileIR, take payloads the toolkit does not
// obviously produce, so they are probed here on their own.
//
// Neither payload needs to be valid for the purpose at hand. The kind field is
// written by the creation library when the entry is added, so if the call
// succeeds at all the kind can be read straight back out of the container. If
// the call rejects the payload, that is reported too, since a rejection is a
// fact about the API rather than a failure of the probe.
#include <nvFatbin.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void report(const char *tag, nvFatbinResult r, nvFatbinHandle h,
                   const char *out)
{
    if (r != NVFATBIN_SUCCESS) {
        const char *msg = nvFatbinGetErrorString(r);
        printf("  %-8s REJECTED: result %d (%s)\n",
               tag, (int)r, msg ? msg : "no message");
        return;
    }
    size_t sz = 0;
    if (nvFatbinSize(h, &sz) != NVFATBIN_SUCCESS) {
        printf("  %-8s accepted, but size query failed\n", tag);
        return;
    }
    unsigned char *buf = malloc(sz);
    if (nvFatbinGet(h, buf) != NVFATBIN_SUCCESS) {
        printf("  %-8s accepted, but get failed\n", tag);
        free(buf);
        return;
    }
    FILE *f = fopen(out, "wb");
    if (f) { fwrite(buf, 1, sz, f); fclose(f); }

    // Container header is 16 bytes: magic u32, version u16, headerSize u16,
    // fatSize u64. The first entry begins at headerSize, and its kind is the
    // u16 at offset 0 of that entry.
    unsigned short hsize = (unsigned short)(buf[6] | (buf[7] << 8));
    unsigned short kind  = (unsigned short)(buf[hsize] | (buf[hsize + 1] << 8));
    printf("  %-8s ACCEPTED -> kind = 0x%x  (%s, %zu bytes)\n",
           tag, kind, out, sz);
    free(buf);
}

int main(void)
{
    // Deliberately synthetic payloads. If the library validates them it will
    // say so, which is itself the answer.
    static const unsigned char blob[64] = { 0 };

    const char *opts[] = { "-32" };
    (void)opts;

    printf("nvFatbinAddIndex and nvFatbinAddTileIR:\n");

    {
        nvFatbinHandle h = NULL;
        nvFatbinResult r = nvFatbinCreate(&h, NULL, 0);
        if (r == NVFATBIN_SUCCESS)
            r = nvFatbinAddIndex(h, blob, sizeof blob, "probe_index");
        report("index", r, h, "out/kind_index.fatbin");
        if (h) nvFatbinDestroy(&h);
    }

    {
        nvFatbinHandle h = NULL;
        nvFatbinResult r = nvFatbinCreate(&h, NULL, 0);
        if (r == NVFATBIN_SUCCESS)
            r = nvFatbinAddTileIR(h, blob, sizeof blob, "probe_tileir", "");
        report("tileir", r, h, "out/kind_tileir.fatbin");
        if (h) nvFatbinDestroy(&h);
    }

    return 0;
}
