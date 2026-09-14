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
| 0x80 | tile ir | `cuobjdump` switch; `nvFatbinAddTileIR` writes it |
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
`nvFatbinAddIndex` is the only `Add*` function in `nvFatbin.h` with no `arch`
parameter, and the driver's filter accepts kind 0x20 without reading the
entry's architecture or flags at all.

Neither `index` nor `tile ir` could be round-tripped into a sample.
`nvFatbinAddIndex` rejects synthetic input with `NVFATBIN_ERROR_INVALID_INDEX`,
and its header says plainly "Currently, no method of creating an index file is
available". `nvFatbinAddTileIR` rejects synthetic input with an internal error,
and this toolkit will not emit TileIR from a `.cu` source. Both are therefore
established by name and by the code that writes the constant, not by building
one.

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

`probes/kind_probe2.c` covers `nvFatbinAddIndex` and `nvFatbinAddTileIR`, the
two that reject synthetic input, and reports the rejection rather than a kind.

## Method

Two independent reverse-engineering passes were run, one on the WSL build
597.06 and one on the data center build 610.57.04, and they agreed on all four
previously unidentified kinds. The `cuobjdump` switch, the `libnvfatbin`
constant and the string evidence were then re-checked by hand against the
binaries.
