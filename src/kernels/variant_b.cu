// variant_b.cu - identical to variant_a.cu except for the marker value.
// Same exported symbol "probe" by design; see variant_a.cu.

extern "C" __global__ void probe(int *out)
{
    out[threadIdx.x] = 0xBBBB;
}
