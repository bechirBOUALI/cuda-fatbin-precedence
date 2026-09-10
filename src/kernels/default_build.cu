// default_build.cu - an ordinary CUDA program (kernel + host main), built with
// no special flags. Used to answer: does a normal build already emit more than
// one entry matching the running GPU?
#include <cstdio>

extern "C" __global__ void probe(int *out)
{
    out[threadIdx.x] = 0xAAAA;
}

int main(void)
{
    int *d = nullptr, h[4] = {0};
    cudaMalloc(&d, sizeof(h));
    probe<<<1, 4>>>(d);
    cudaMemcpy(h, d, sizeof(h), cudaMemcpyDeviceToHost);
    std::printf("marker=0x%04X\n", h[0]);
    cudaFree(d);
    return 0;
}
