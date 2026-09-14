# "PTX Obfuscation": what the string in the driver means

Investigated 2026-09-14. The container walk in `libcuda.so.1.1` references two
strings, `PTX Obfuscation` and `TileIR Obfuscation`. This note establishes what
they are, because the answer matters to anything that reads GPU code
statically.

## Where they appear

| Where | Detail |
|---|---|
| WSL driver 597.06 | `PTX Obfuscation` at VA `0x150eb54`, `TileIR Obfuscation` at `0x150eb64` |
| H100 driver 610.57.04 | the same two strings, at file offsets `0x67cbe48` and `0x67cbe58` |
| `nvlink` | both strings, alongside `NVVM` and `Error reading obfuscated PTX file` |

In the driver both are passed as the second argument to the function at
`0x490490`, from three sites inside the container walk: `0x47b20e`, `0x47bcc7`
and `0x47bebd`. That function is a varargs logger, `f(channel*, fmt, ...)`,
recognisable from its register-save prologue and from reading a level and an
enabled flag out of the struct it is handed. So the strings are log labels, not
data the parser acts on.

The path is guarded. At `0x47b120` the driver tests a pointer at `state+0x98`
and only takes the logging branch when it is non-null, so an obfuscation
context has to be present for any of this to run.

## What the feature is

Not a driver curiosity. The CUDA toolkit ships a keyed obfuscation scheme for
fat binary contents, and the strings in the driver are the names of its two
domains. Evidence, all from the shipped 13.2 toolkit:

| Tool | String |
|---|---|
| `fatbinary`, `nvcc` | "This option will cause all compute 'binaries' to be obfuscated using the specified key" |
| `fatbinary`, `libnvfatbin` | `reorder-obfuscation` |
| `nvlink` | `PTX Obfuscation`, `TileIR Obfuscation` |
| `ptxas` | `Error reading obfuscated PTX file` |
| `cuobjdump` | `Can't deobfuscate entry '%s' without obfuscation key` |
| `nvdisasm` | `Unable to create obfuscation state.` |

`fatbinary` takes `--okey=<number>` for the key and `-reorder-obfuscation` to
apply it.

## Measured behaviour

Building the same PTX three ways, on this toolkit:

| Build | entry kind | entry flags | payload |
|---|---|---|---|
| plain | 1 | `0x8011` | zstd, decompresses to readable PTX |
| `--okey=12345` | 1 | `0x8011` | zstd, still readable PTX; only 3 header bytes change |
| `--okey=12345 -reorder-obfuscation` | 1 | **`0x18011`** | not a valid zstd frame; 234 bytes differ |

So obfuscation is a **flag, not a kind**. The entry stays kind 1, and bit 16
of the entry `flags` field marks it. That is a third distinct use of that
field, alongside bits 20 and 21 for the architecture-name suffix and bit 24 for
the cubin tie-break.

The key alone does nothing to the payload; `-reorder-obfuscation` is what
transforms it. Applying both to a **cubin** entry leaves flags at `0x11` and
changes nothing, which matches the feature being named for PTX and TileIR
rather than for compiled binaries.

## The key is stored in the container

This is the part that undercuts the feature. The 8-byte field at entry+0x30,
which is zero in every ordinary build and which format notes usually call
reserved, holds the key. It is encoded as BCD: the decimal digits of the value
given to `--okey`, read as hex nibbles, little-endian.

| `--okey` | field at entry+0x30 |
|---|---|
| 99 | `0x99` |
| 100 | `0x100` |
| 12345 | `0x12345` |
| 65535 | `0x65535` |
| 1000000 | `0x1000000` |

So the entry header is not "reserved" there, and a reader holding only the file
holds the key as well. What that means for the strength of the scheme is being
checked separately; it may be that the value is an identifier rather than the
secret, or that the transform is reversible from it. Either way the field
should be recorded as the key and not as padding.

Very large values are refused by `fatbinary`; 987654321 and 4294967295 both
fail to build.

## What each side can see

For an obfuscated entry, with no key supplied:

```
$ cuobjdump -lptx ptx_reorder.fatbin
cuobjdump warning : Can't deobfuscate entry '...' without obfuscation key
cuobjdump info    : No PTX file found to extract

$ cuobjdump -ptx ptx_reorder.fatbin
arch = sm_89
code version = [9,2]
host = linux
compile_size = 64bit
compressed
```

The entry's **metadata is fully readable** while its **code is not**. A tool
sees an entry for sm_89 at PTX ISA 9.2 and cannot see a single instruction of
it.

The driver refuses it too: loading that container returns
`CUDA_ERROR_INVALID_PTX`. That is the puzzle this leaves. The key is sitting in
the entry header, so the driver has everything the format gives it and still
declines, and there is no obfuscation-key option in `cuda.h` for an application
to supply one another way. Either the driver does not implement the reverse
transform at all, or something beyond the key is required. Which of those it is
has not been established here.

## Why it matters here

This is a legitimate IP-protection feature, and it is not a vulnerability. It
is worth recording because it is the strongest form of the gap this repository
is about. The rest of this work is concerned with a scanner reading the *wrong*
entry. An obfuscated entry is a case where the scanner can read *no* entry: the
container advertises architecture, ISA version and size correctly, and the code
itself is opaque without a key the scanner does not have.

Any policy of the form "extract the PTX and inspect it" has to account for the
case where the PTX cannot be extracted at all, and should treat that as a
distinct outcome rather than as an empty result. `cuobjdump` reports it as a
warning on one line and "No PTX file found" on the next, which is easy to
mistake for an entry that simply has no PTX.

## Open

Given that the key sits in the file, what the obfuscation actually protects,
and whether the embedded value is the secret or merely names one. Why the
driver refuses an obfuscated entry when the key is right there in the
container. What `state+0x98` is. And whether TileIR is treated the same as PTX,
which the second log label suggests but which no sample could confirm, since
this toolkit would not emit TileIR.
