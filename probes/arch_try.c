#include <nvFatbin.h>
#include <stdio.h>
#include <stdlib.h>
static void *slurp(const char *p, size_t *n, int nul){
    FILE *f=fopen(p,"rb"); fseek(f,0,SEEK_END); long s=ftell(f); fseek(f,0,SEEK_SET);
    char *b=malloc(s+1); if(fread(b,1,s,f)!=(size_t)s) exit(1); fclose(f); b[s]=0;
    *n = nul ? (size_t)s+1 : (size_t)s; return b; }
int main(void){
    const char *opts[]={"-cuda"};
    const char *archs[]={"compute_89","sm_89","89","compute_90","lto_89"};
    size_t np,nc; void *ptx=slurp("../build/variant_a.ptx",&np,1);
    void *cub=slurp("../build/variant_a.cubin",&nc,0);
    for(int i=0;i<5;i++){
        nvFatbinHandle h; nvFatbinResult r;
        nvFatbinCreate(&h,opts,1);
        r=nvFatbinAddPTX(h,ptx,np,archs[i],"p","");
        printf("  AddPTX   arch=%-12s -> %d\n",archs[i],(int)r);
        nvFatbinDestroy(&h);
        nvFatbinCreate(&h,opts,1);
        r=nvFatbinAddCubin(h,cub,nc,archs[i],"c");
        printf("  AddCubin arch=%-12s -> %d\n",archs[i],(int)r);
        nvFatbinDestroy(&h);
    }
    return 0; }
