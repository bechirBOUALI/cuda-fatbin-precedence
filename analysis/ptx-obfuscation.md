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

The driver is in the same position without the key: loading that container
returns `CUDA_ERROR_INVALID_PTX`. There is no obfuscation-key option in
`cuda.h`, and the driver carries no deobfuscation strings beyond the two
labels, so the key evidently reaches the JIT by some path an ordinary
application supplies rather than being embedded in the container.

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

What `state+0x98` is and how the key reaches the driver. The two log labels
suggest TileIR gets the same treatment as PTX, but no TileIR sample could be
produced with this toolkit, so that is unconfirmed.
