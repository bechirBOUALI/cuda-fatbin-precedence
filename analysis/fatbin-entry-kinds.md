# Fatbin entry kinds

Each entry carries a `kind` field, a u16 at offset 0. The driver's code
references the values 1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80 and 0x100. This
document identifies them.

Two independent sources settle almost all of it. `cuobjdump` carries a
kind-to-name switch and prints NVIDIA's own name for each value, and
`libnvfatbin` exposes one `Add*` function per input type, so a container can be
built with a known input and the kind read back.

## Results

| kind | NVIDIA's name | How established |
|---|---|---|
| 1 | ptx | `nvFatbinAddPTX`; `cuobjdump` switch |
| 2 | elf | `nvFatbinAddCubin`; `cuobjdump` switch |
| 4 | cubin | `cuobjdump` switch |
| 8 | nvvm (LTO IR) | `nvcc -dlto` object; `cuobjdump` switch |
| 0x10 | **none** | `cuobjdump` prints `<unknown kind>`; see below |
| 0x20 | index | `cuobjdump` switch; `nvFatbinAddIndex` writes it |
| 0x40 | relocatable ptx | `nvFatbinAddReloc`; `cuobjdump` switch |
| 0x80 | tile ir | `nvFatbinAddTileIR` on real Tile IR; `cuobjdump` switch and output; see below |
| 0x100 | contatenated entry | `cuobjdump` switch; the spelling is NVIDIA's |

The switch is at `0x2a895` onward in the CUDA 13.2 build of `cuobjdump`, and
each comparison is followed by the address of its label:

```
2a895:  cmp    $0x8,%ax
2a8a5:  cmp    $0x40,%ax   -> 0x8c3ec "relocatable ptx"
2a8b8:  cmp    $0x80,%ax   -> 0x8c3fc "tile ir"
2a8c5:  cmp    $0x100,%ax  -> 0x8c404 "contatenated entry"
2a8d2:                        0x8c417 "<unknown kind>"
2b2e2:  cmp    $0x20,%ax   -> 0x8c8ec "index"
```

`contatenated entry` also describes 0x100 better than "group" would: the driver
walks it as a counted table of nested entry headers whose offset is the u32 at
entry+0x14.

`nvFatbinAddIndex` writing kind 0x20 is visible directly, `mov $0x20,%ecx` at
`0xb11f6` in `libnvfatbin.so.13.2.86`. That matches the API shape, since
`nvFatbinAddIndex` takes no `arch` parameter and the driver's filter accepts
kind 0x20 without reading the entry's architecture or flags at all. Three of
the six `Add*` functions take no `arch`: `nvFatbinAddIndex`,
`nvFatbinAddReloc` and `nvFatbinAddTileIR`. For Tile IR the architecture is
carried inside the bytecode instead, as the sample below shows.

`index` could not be round-tripped into a sample. `nvFatbinAddIndex` rejects
synthetic input with `NVFATBIN_ERROR_INVALID_INDEX`, and its header says plainly
"Currently, no method of creating an index file is available", so kind 0x20 rests
on the name and on the code that writes the constant rather than on a built
container.

`tile ir` does have a sample. The earlier failure was the input, not the API:
`nvFatbinAddTileIR` rejects a synthetic blob with an internal error, and NVRTC
will not emit Tile IR from a `.cu` source under any option spelling tried in
`probes/tile_try.c`. Real Tile IR comes from elsewhere in the toolkit, and the
call accepts it.

## Kind 0x80, built from real Tile IR

The toolkit ships a Tile IR producer; it is just not reachable from `.cu`
source. `tileiras`, which identifies itself as the "NVIDIA (R) Cuda Tile IR
optimizing assembler", takes a tile bytecode file and writes a cubin:

```
tileiras --gpu-name sm_89 --opt-level 3 -o out.cubin in.bc
```

`cutile-rs` drives exactly that: its pipeline writes tile bytecode to a
temporary file and invokes `tileiras` on it, which is where a real payload can
be obtained. The bytecode carries its own magic, `\x7fTileIR\0`:

```
00000000: 7f54 696c 6549 5200 0d02 0000 8280 0208  .TileIR.........
```

Handing 1895 bytes of that bytecode to `nvFatbinAddTileIR` succeeds where the
synthetic blob failed, and the container returned carries kind 0x80:

```
input 1895 bytes, first4 7f 54 69 6c
nvFatbinCreate     -> 0
nvFatbinAddTileIR  -> 0
nvFatbinSize       -> 0, 872 bytes
header_size 0x10, FIRST ENTRY KIND = 0x80
```

`cuobjdump` reads the same container back under NVIDIA's own name, which
confirms the switch-table reading against a real sample rather than against a
constant:

```
Fatbin tile ir code:
====================
arch = sm_80
code version = [3330,0]
host = linux
compile_size = 64bit
compressed
identifier = real_tile
tileIROptions =
```

Three details from the container itself, entry at 0x10, header 0x60 bytes:

- Flags at entry+0x28 are 0x8015, so bit 0x8000 is set and the payload is
  zstd-compressed, 1895 bytes in and 755 out. Nothing asked for compression.
  This is the same point the incidental findings below make for ELF: read the
  flag, do not infer from the kind.
- The decompressed size at entry+0x38 is 0x767, which is the 1895 input bytes
  exactly.
- The arch reads `sm_80` although the bytecode was produced under `tileiras
  --gpu-name sm_89`. Since `nvFatbinAddTileIR` has no `arch` parameter, the
  value comes from the bytecode. Why it is sm_80 was not traced.

Neither Rust framework ships such a container, which is worth stating since
both were checked for it. Under gdb at `cuModuleLoadData`, `cuda-oxide` hands
over PTX source text by default and an ELF cubin under `--materialize-cubin`,
and `cutile-rs` hands over the cubin that `tileiras` produced. Neither builds a
fat binary at any point, so neither exercises the selection rule. The kind 0x80
container above is constructed from `cutile-rs`'s intermediate, not shipped by
it.

## Kind 0x10 is unnamed

NVIDIA names it nowhere on this machine: no case in `cuobjdump`, no `Add*`
function in `libnvfatbin`, no sixth name in `fatbinary`'s kind list, and no
kind-to-name table in the driver.

What the driver's code shows is that it is an ELF variant requiring work before
it can load. It has its own options-string slot in the selector state, separate
from those for PTX, NVVM and TileIR. The load path dispatches it to a handler
whose entire error vocabulary is the Mercury finalizer: "Invalid elf provided
for mercury uplift", "Self check for capsule mercury text section failed", "the
elf arch is not compatible with finalizer arch", "SASS generation failed". The
toolkit corroborates: `ptxas` carries `<mercury|capmerc|sass>`, "Generate
Capsule Mercury" and `EIATTR_MERCURY_FINALIZER_OPTIONS`. In the data center
driver, 79 of the 335 embedded device ELFs carry `.nv.capmerc` or `.nv.merc`
sections, and exactly those have the `e_flags` bit the handler tests.

Best supported reading, **labelled as inference rather than fact**: an
unfinalized device ELF that the driver finalizes before execution. That fits
its rank between ELF and PTX, its architecture-specific matching and its
separate options string. NVIDIA's own name for it remains unknown.

## Incidental findings from the creation API

**The architecture string is a bare number.** `nvFatbinAddPTX` and
`nvFatbinAddCubin` accept `"89"` and reject `"sm_89"`, `"compute_89"` and
`"lto_89"` with `NVFATBIN_ERROR_INVALID_ARCH`. This is not stated in the
header.

**Compression is not implied by kind.** In an ordinary build the ELF payload is
raw, but in an `-rdc=true` object it is zstd-compressed. A parser must read
flag bit 0x8000 rather than infer from the kind.

**The creation API validates architecture against the payload.** The error enum
includes `NVFATBIN_ERROR_ELF_ARCH_MISMATCH` and
`NVFATBIN_ERROR_PTX_ARCH_MISMATCH`, so a container whose entry header disagrees
with its embedded ELF cannot be built with this API. It has to be constructed
by hand, which is how `entry-precedence.md` produces that case.

## Reproduction

```sh
gcc -O1 -o kind_probe probes/kind_probe.c -I/usr/local/cuda-13.2/include \
    -L/usr/local/cuda-13.2/lib64 -lnvfatbin
LD_LIBRARY_PATH=/usr/local/cuda-13.2/lib64 ./kind_probe
```

`probes/kind_probe2.c` covers `nvFatbinAddIndex` and `nvFatbinAddTileIR` on
synthetic input, and reports the rejection rather than a kind.
`probes/real_tile.c` takes a tile bytecode file as its argument, adds it with
`nvFatbinAddTileIR`, writes `real_tile.fatbin` and prints the kind it reads
back. Capture the bytecode by putting a wrapper ahead of
`/usr/local/cuda-13.2/bin/tileiras` on `PATH` that copies its `.bc` argument
aside, then running any `cutile-rs` example.

## Method

Two independent reverse-engineering passes were run, one on the WSL build
597.06 and one on the data center build 610.57.04, and they agreed on all four
previously unidentified kinds. The `cuobjdump` switch, the `libnvfatbin`
constant and the string evidence were then re-checked by hand against the
binaries.
