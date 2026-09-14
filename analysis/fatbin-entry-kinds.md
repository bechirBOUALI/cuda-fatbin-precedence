# Fatbin entry kinds, identified by construction

Tested 2026-09-10. The reverse engineering in `driver-selection-logic.md`
recovered a `kind` field at offset 0 of each entry and observed the values
1, 2, 8, 0x10, 0x20, 0x40, 0x80 and 0x100 in the driver's code, but could only
identify two of them. NVIDIA's `nvFatbin.h` creation API provides a way to
settle the rest empirically: build a fatbin with a known input type, then read
the kind back.

## Method

`nvFatbin.h` (library `libnvfatbin.so`, ships with the toolkit) exposes one
`Add*` function per input type. The probe at `re/kind_probe.c` creates one
fatbin per function and writes it out; the kind field is then read with the
entry layout recovered by RE.

LTO IR was obtained differently, by compiling with `nvcc -arch=lto_89 -dlto`
and reading the kind out of the resulting object directly.

## Results

| kind | Meaning | How identified |
|---|---|---|
| 1 (0x1) | PTX | `nvFatbinAddPTX` |
| 2 (0x2) | ELF / cubin | `nvFatbinAddCubin` |
| 8 (0x8) | LTO IR (NVVM) | `nvcc -dlto` object |
| 0x40 (64) | Relocatable PTX from a host object | `nvFatbinAddReloc` |

Kinds 1 and 2 confirm the hypothesis and match the RE. Kinds 8 and 0x40 were
listed as unidentified by both RE passes and are now pinned.

### Still unidentified

- **0x10, 0x20, 0x80.** No known construction path, and none of the three is
  reachable through the public `nvFatbin.h` API. The reverse engineering did
  establish where 0x10 sits in the driver's preference order, between ELF (2)
  and PTX (1), but not what it represents. Identifying it would need either a
  construction path or further work on the driver's handler dispatch.
- **0x100.** The Ghidra pass read this as a nested group or index entry. NVIDIA
  documents `nvFatbinAddIndex` with the note "Currently, no method of creating
  an index file is available", so it cannot be produced and the reading stays
  unverified. The documentation and the RE are at least consistent.
- **Tile IR.** `nvFatbinAddTileIR` exists but Tile IR appears only in
  `nvrtc.h` and `nvFatbin.h`, with no nvcc path. Untested.

## Incidental findings

**The API's architecture string is a bare number.** `nvFatbinAddPTX` and
`nvFatbinAddCubin` accept `"89"` and reject `"sm_89"`, `"compute_89"` and
`"lto_89"` with `NVFATBIN_ERROR_INVALID_ARCH`. This is not stated in the header
and cost several attempts to find.

**Flag bit 0x8000 means compressed.** Across every sample, entries whose
payload begins with the Zstandard magic have it set and entries beginning with
`\x7fELF` do not. LTO IR additionally carries bit 0x10000.

**Compression is not implied by kind.** In an ordinary build the ELF payload is
raw, but in an `-rdc=true` object the ELF payload is zstd-compressed. A parser
must read the flag rather than infer from entry kind.

**The creation API validates architecture against the payload.** The error enum
includes `NVFATBIN_ERROR_ELF_ARCH_MISMATCH` and `NVFATBIN_ERROR_PTX_ARCH_MISMATCH`.
So the "entry header architecture disagrees with the embedded ELF" conflict case
still open in `step1-entry-precedence.md` cannot be built with this API either.
It has to be constructed by hand, like the other remaining cases.

## Reproduction

```sh
cd re
gcc -O1 -o kind_probe kind_probe.c -I/usr/local/cuda-13.2/include \
    -L/usr/local/cuda-13.2/lib64 -lnvfatbin
LD_LIBRARY_PATH=/usr/local/cuda-13.2/lib64 ./kind_probe
```

## The remaining kinds, identified 2026-09-14

The four kinds left unidentified above are now settled, and the decisive
evidence is not in the driver at all. `cuobjdump` carries a kind-to-name switch
and prints the name for each, which makes this NVIDIA's own naming rather than
an inference.

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

| kind | NVIDIA's name | How confirmed |
|---|---|---|
| 0x20 | `index` | `cuobjdump` switch, and `nvFatbinAddIndex` writes 0x20 |
| 0x80 | `tile ir` | `cuobjdump` switch, and `nvFatbinAddTileIR` writes 0x80 |
| 0x100 | `contatenated entry` | `cuobjdump` switch; the typo is NVIDIA's |
| 0x10 | **none** | `cuobjdump` has no case for it and prints `<unknown kind>` |

The spelling `contatenated` is quoted as it appears. It also corrects the
earlier reading of 0x100 as a "group" entry: the driver walks it as a counted
table of nested entry headers, so "concatenated" describes it better.

`nvFatbinAddIndex` writing kind 0x20 is visible directly, `mov $0x20,%ecx` at
`0xb11f6` in `libnvfatbin.so.13.2.86`, on the path that appends the entry. That
independently confirms the `cuobjdump` name, and it matches the API shape:
`nvFatbinAddIndex` is the only `Add*` function in `nvFatbin.h` with no `arch`
parameter, and the driver's filter accepts kind 0x20 without reading the
entry's architecture or flags at all.

Neither `index` nor `tile ir` could be produced here. `nvFatbinAddIndex`
rejects synthetic input with `NVFATBIN_ERROR_INVALID_INDEX`, and the header
says plainly "Currently, no method of creating an index file is available".
`nvFatbinAddTileIR` rejects synthetic input with an internal error, and this
toolkit would not emit TileIR from a `.cu` source. So these two are confirmed
by name and by the code that writes them, not by round-tripping a sample.

### Kind 0x10 remains unnamed

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

Best supported reading, and labelled as inference rather than fact: **an
unfinalized device ELF that the driver finalizes before execution**. That fits
its rank between ELF and PTX, its architecture-specific matching, and its
separate options string. NVIDIA's own name for it is unknown.

### Method note

Two independent reverse-engineering passes were run, one on the WSL build
597.06 and one on the data center build 610.57.04, and they agreed on all four
kinds. The `cuobjdump` switch, the `libnvfatbin` constant and the string
evidence were then re-checked by hand against the binaries before being
recorded here.
