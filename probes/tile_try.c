#include <nvrtc.h>
#include <stdio.h>
static const char *SRC="extern \"C\" __global__ void probe(int*o){o[threadIdx.x]=1;}\n";
int main(void){
  const char *cands[]={"--enable-tile","--tile-only","--enable-tile=true","-enable-tile","--enable-tile=1","--gen-tile-ir"};
  for(int i=0;i<6;i++){
    nvrtcProgram p; nvrtcCreateProgram(&p,SRC,"p.cu",0,0,0);
    const char *o[]={"--gpu-architecture=compute_89",cands[i]};
    nvrtcResult r=nvrtcCompileProgram(p,2,o);
    size_t ts=0; nvrtcResult tr=nvrtcGetTileIRSize(p,&ts);
    printf("  %-20s compile=%-2d tileIRSize=%d size=%zu\n",cands[i],(int)r,(int)tr,ts);
    nvrtcDestroyProgram(&p);
  }
  return 0;}
