// variant_a.cu - device code only. No host code, no main().
// Compiled standalone to .cubin/.ptx and loaded at runtime by the harness.
//
// variant_a and variant_b export the SAME symbol "probe" and differ only in the
// marker they write. That is what lets the harness tell which entry the driver
// actually selected out of a fatbin containing both.

extern "C" __global__ void probe(int *out)
{
    out[threadIdx.x] = 0xAAAA;
}
