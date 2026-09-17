# "PTX Obfuscation": what the string in the driver means

The container walk in `libcuda.so.1.1` references two strings, `PTX
Obfuscation` and `TileIR Obfuscation`. This note establishes what they are,
because the answer matters to anything that reads GPU code statically.

## Where they appear

| Where | Detail |
|---|---|
| WSL driver 597.06 | `PTX Obfuscation` at VA `0x150eb54`, `TileIR Obfuscation` at `0x150eb64` |
| H100 driver 610.57.04 | the same two strings, at file offsets `0x67cbe48` and `0x67cbe58` |
| `nvlink` | both strings, alongside `NVVM` and `Error reading obfuscated PTX file` |

In the driver both are passed as the second argument to the varargs logger at
`0x490490`, from three sites inside the container walk: `0x47b20e`, `0x47bcc7`
and `0x47bebd`. The first argument is a descriptor carrying the format string,
which turns out to be "Feature: '%s' not yet implemented", so these two strings
are feature names substituted into that message rather than messages in their
own right. The evidence is below, under what each side can see.

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
| `--okey=12345` | 1 | `0x8011` | zstd, still readable PTX; only the key field changes |
| `--okey=12345 -reorder-obfuscation` | 1 | **`0x18011`** | not a valid zstd frame; 234 bytes differ |

So obfuscation is a **flag, not a kind**. The entry stays kind 1, and bit 16
of the entry `bin_info` field marks it. That is a third distinct use of that
field, alongside bits 20 and 21 for the architecture-name suffix and bit 24 for
the cubin tie-break.

The key alone does nothing to the payload; `-reorder-obfuscation` is what
transforms it. Applying both to a **cubin** entry leaves flags at `0x11` and
changes nothing, which matches the feature being named for PTX and TileIR
rather than for compiled binaries.

## The key is stored in the container

The 8-byte field at entry+0x30, zero in every ordinary build and easily taken
for padding, holds the key.

The stored value looks at first like BCD, the decimal digits of the key read as
hex nibbles: `--okey=12345` stores `0x12345`, `--okey=1000000` stores
`0x1000000`. It is not a deliberate encoding. `fatbinary` parses the option as a
**32-bit hex value**, which is visible in its own diagnostics, `--okey=DEADBEEF`
is rejected with "'DEADBEEF': expected a number" and `--okey=4294967296` with
"32-bit hex value (4294967296) out of range", so the argument is filtered to
decimal digits
on the way in and read as hex on the way out. The round trip is what produces
the digit-preserving pattern, and two inputs settle it:

| `--okey` | stored |
|---|---|
| 0x1234 | `0x4660` |
| 4660 | `0x4660` |

`0x1234` is 4660, re-emitted as "4660", read back as `0x4660`. So the two forms
collide.

The effective key space is smaller than it looks, for a reason the collision
only hints at. Every stored nibble is a decimal digit, because the stored value
is the decimal spelling of the key read as hex, so the reachable stored values
number at most 10 to the power of the digit count rather than 16. The digit
count is bounded by what `fatbinary` accepts, measured here rather than
assumed: `--okey=99999999` builds, `--okey=100000000` and
`--okey=4294967295` are refused. Eight decimal digits is under 2^27 distinct
keys, well below the 32 bits the field width suggests. None of which matters
much, since the key is stored in the container anyway.

## The obfuscation does not protect the code

The transform is reversible from the file alone, because the file carries the
key. Verified here by recovering the plaintext: the payload is transformed with
a keyed byte-wise stream cipher using a fixed substitution table that ships
inside `libnvfatbin`, with ciphertext feedback chaining each byte to the
previous one, and a keystream from the ordinary C library `rand()` generator
seeded with the key. Everything needed is either in the container or in a
library on any machine with the toolkit installed.

Reimplementing that from the shipped tables recovers the original PTX in full,
including the kernel body and its marker constant, from
`fatbinary --okey=12345 -reorder-obfuscation` output. The first four recovered
bytes are the zstd magic, and decompressing gives back readable PTX.

The conclusion is that `--okey` is a speed bump, not a protection boundary. It
stops a tool that has not been taught the format; it stops nothing else. That
is worth stating plainly because the option's own help text says it will cause
binaries "to be obfuscated using the specified key", which invites more
confidence than the construction supports.

A working recovery script was written to confirm this and is deliberately not
included in this repository. The description above is sufficient for anyone who
needs to reproduce the analysis.

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

The entry's **metadata is fully readable** while its **code is not**, at least
as far as the shipped tools are concerned. `cuobjdump` has no way to accept a
key at all: it tests the flag bit, warns, and never reads the key field sitting
in the entry header. So the message naming a missing key is misleading, and a
tool that does implement the transform needs nothing from the user.

The driver refuses it too, and it says why. Loading the container returns
`CUDA_ERROR_INVALID_PTX`, and the two strings in the walk are not labels after
all: they are the `%s` argument to one message. The descriptor they are passed
with, at `0x181a210`, is filled in by a relocation:

```
readelf -rW libcuda.so.1.1
000000000181a218  R_X86_64_RELATIVE  150fb18
000000000150fb18  "Feature: '%s' not yet implemented"
```

So the driver logs **"Feature: 'PTX Obfuscation' not yet implemented"**. It
recognises an obfuscated entry, and declines to handle it. The refusal is not a
missing key; the reverse transform is simply not implemented in this driver.
Two neighbouring strings round out the picture: "Can't load this binary kind,
as it's not recognized" and "Can't JIT TileIR without libtileiras".

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

What `state+0x98` is beyond holding the key. Whether TileIR is obfuscated the
same way as PTX, which the second feature name implies but which no sample
could confirm, since this toolkit would not emit TileIR. And whether any
consumer anywhere implements the reverse transform, since neither `cuobjdump`
nor the driver does.
