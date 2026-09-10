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
