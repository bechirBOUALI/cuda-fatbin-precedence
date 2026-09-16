# What a container declares is not what the GPU executes

`entry-precedence.md` answers which entry the driver selects. This document
answers the question underneath it: once an entry is selected, which bytes are
that entry. The container states two sizes and neither means what a reader
would assume, so a tool can hash the right entry and still hash the wrong
bytes.

Both cases were open questions when the precedence rule was first measured.
They are settled here, by execution.

| | |
|---|---|
| GPU | NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9 |
| Driver | 597.06, reached through the WSL2 stub |
| Toolkit | CUDA 13.2 |
| Method | every container loaded with `CUDA_CACHE_DISABLE=1`, marker read back |

## Field names follow Stealthium's published struct

The format was documented publicly before this work, so this repository uses
those names rather than inventing a second vocabulary. The parser declares its
structs with them and every document here follows. Two fields that write-up
marks undocumented are named for what they were measured to hold, and both are
marked in bold below.

Container header:

| offset | width | name |
|---|---|---|
| 0x00 | u32 | `magic`, `0xBA55ED50` |
| 0x04 | u16 | `version` |
| 0x06 | u16 | `header_size` |
| 0x08 | u64 | `fatbin_size` |

Entry header:

| offset | width | name | in the published struct |
|---|---|---|---|
| 0x00 | u16 | `kind` | same |
| 0x02 | u16 | `version`, `0x0101` throughout | same |
| 0x04 | u32 | `header_size` | same |
| 0x08 | u64 | `padded_payload_size` | same |
| 0x10 | u32 | `payload_size` | same |
| 0x14 | u32 | `ptxas_options_offset` | same |
| 0x18, 0x1a | u16 | `code_version_minor`, `code_version_major` | same |
| 0x1c | u32 | `arch` | same |
| 0x20 | u32 | `identifier_offset` | same |
| 0x24 | u32 | **`identifier_length`** | `field_24`, undocumented |
| 0x28 | u64 | `bin_info` | same |
| 0x30 | u64 | **`obfuscation_key`** | `field_30`, undocumented |
| 0x38 | u64 | `uncompressed_payload` | same |

Two of those are identifications rather than renamings, and each was swept
rather than sampled.

`field_24` is the length of the identifier string at `identifier_offset`. Five
containers built with `fatbinary --ident=` at different lengths:

| identifier length | 1 | 2 | 10 | 13 | 40 |
|---|---|---|---|---|---|
| `field_24` | 1 | 2 | 10 | 13 | 40 |
| `identifier_offset` | 64 | 64 | 64 | 64 | 64 |
| entry `header_size` | 72 | 72 | 80 | 80 | 112 |

It tracks the string length exactly, with no terminator and no padding, while
the offset stays pinned at the end of the fixed header and `header_size` grows
to hold the string.

`field_30` holds the obfuscation key, the decimal digits of `fatbinary --okey`
read back as hex: `--okey=1` stores `0x1`, `--okey=12345` stores `0x12345`,
`--okey=99999` stores `0x99999`. One caveat matters for anyone keying on it:
in all three the `bin_info` flags stayed `0x8011`, with bit 16 clear, because
the key is recorded whether or not `-reorder-obfuscation` actually transforms
the payload. A non-zero `field_30` therefore does not mean the entry is
obfuscated; bit 16 means that. `ptx-obfuscation.md` measures the transform
itself.

The two size fields are the reason this document exists, so keep them apart.
`padded_payload_size`, the u64 at 0x08, advances the walk. `payload_size`, the
u32 at 0x10, is the compressed stream's real length and is 0 whenever the
payload is stored raw, so for an uncompressed entry the u64 is the only length
a reader has. Where the text below says an entry declares 200 bytes, that is
the u64 at 0x08.

One name is worth questioning rather than mapping. `fatbin_size` is described
as the total size of the contained binaries, which is the natural reading and
the one a parser implements. The driver does not treat it that way. It
truncates the value to a signed 32-bit quantity and uses it only to bound how
far the walk advances from `base + 16`, so an entry whose start falls inside
that bound is walked in full even when its body lies far outside it. The bound
has to be strictly positive but need not be meaningful: `fatbin_size = 0` is
refused with `CUDA_ERROR_NO_BINARY_FOR_GPU`, while `fatbin_size = 1` loads and
runs a 3112-byte entry that the declared size covers one byte of.

## The two rules

**`padded_payload_size`, the u64 at 0x08, advances the walk. It does not bound
the read.**

| Kind | What the driver reads |
|---|---|
| PTX, stored raw | from the payload start to the first NUL, however long `padded_payload_size` says |
| PTX or ELF, compressed | the stream at 0x10 bytes long, decompressed, whatever `padded_payload_size` says. Setting it to zero on a zstd entry still runs the kernel |
| ELF, stored raw | the extent the embedded ELF's own headers describe. Bytes past the declared size are read when present, though not required |

For an ELF that extent is `max(e_phoff + e_phnum * e_phentsize, e_shoff +
e_shnum * e_shentsize, the end of the last section with contents, the end of
the last segment)`. The program header term is not optional: in these cubins
the program header table sits last, so an implementation that stops at the
section header table under-reads by 168 bytes and hashes the wrong extent.

The ELF case is weaker than the PTX case and the difference is worth stating,
because it bounds what an attacker can do with it. Declaring 2935 of a real
3112 and bounding the walk to that one entry, so that the declared bytes are
byte-identical in all four files, `sha256 64c85fd92448`:

| the bytes past the declared size | result |
|---|---|
| left intact | runs |
| zeroed | runs |
| set to `0xff` | `CUDA_ERROR_INVALID_IMAGE` |
| physically absent, file truncated | runs |

So the driver reads past the declared payload when those bytes are there, and
an attester hashing only the declared bytes misses content that decides whether
the entry loads at all. It does not require them, and the declared size has a
floor: take it far enough below the end of the section header table and the
entry is refused outright, with a band near the floor whose edges move between
runs. No ELF analogue of the PTX collision above was constructed, because the
executed code sits inside the region the declared size has to cover. The sharp
version of this finding is the PTX one; the ELF version is that declared bytes
and executed bytes are not the same set.

**`fatbin_size` bounds the walk, twice removed from what it says.** The driver
truncates it to a signed 32-bit value, and an entry is walked when its **start**
lies inside that bound. The entry's header and payload may lie entirely outside
the container, and the driver reads them anyway.

## Two containers, the same declared bytes, different kernels

`ptx_collide_a.fatbin` and `ptx_collide_b.fatbin` each hold one PTX entry
declaring 200 bytes of a 344-byte payload. The declared 200 bytes are
byte-identical in the two files, `sha256 5f9458539df4b732…` for both. The
marker constant sits past byte 200.

```
$ CUDA_CACHE_DISABLE=1 ./build/loader build/ptx_collide_a.fatbin
marker  : 0xAAAA -> variant_a
$ CUDA_CACHE_DISABLE=1 ./build/loader build/ptx_collide_b.fatbin
marker  : 0xBBBB -> variant_b
```

A tool hashing `payload[0 : padded_payload_size]` gives both files the same hash and
the GPU runs different code. That is a hash collision produced by metadata
alone, with no work on the hash function, and it is the sharpest form of the
gap this repository is about.

The claim is bounded, and the bound is worth stating rather than leaving to be
found. What collides is the per-entry hash taken over the declared payload. The
two files differ in the bytes past that payload, so a hash over the whole
container tells them apart: `e381c0ab1b78` against `7cf18119d50f`. A pipeline
that hashes the container as well as its entries still sees a difference at the
container layer. What it cannot do is say which kernel ran, because the entry
hash it would use to answer that is the one that collides.

The limit case removes the remaining doubt. `ptx_declared0.fatbin` declares a
payload of **zero** bytes and runs a complete kernel, so a declared-size hasher
hashes nothing at all while the GPU executes 342 bytes of PTX.

## The same field in the other direction

`hidden_cubin.fatbin` widens the declared payload of a valid entry by 3112
bytes and appends a complete second cubin inside it. The driver reads the
embedded ELF's own extent and never touches the appendix.

`swallow_entry.fatbin` widens entry 0's declared payload so the walk steps over
entry 1 entirely. Two bytes of metadata change, no payload byte changes, and
one entry disappears from every reader. `swallow_bit24.fatbin` does the same to
a container where entry 1 was the winner, so the executed kernel flips from
`0xBBBB` to `0xAAAA` on a two-byte edit at file offset 0x18, which is the low
half of the first entry's `padded_payload_size`; every other offset in this
document is entry-relative.

## Entries outside the declared container

The boundary is exact and one byte wide. On a container whose entry 1 wins:

| declared `fatbin_size` | driver | `cuobjdump -lelf` |
|---|---|---|
| 3176 | runs entry 0 | lists 1 |
| **3177** | **runs entry 1** | lists 1 |
| 3239 | runs entry 1 | lists 1 |
| 3240 | runs entry 1 | lists 2 |

The driver needs the entry's start inside the bound; `cuobjdump` needs its
whole 64-byte header to fit. Between the two lies a 63-byte window in which the
GPU executes an entry no NVIDIA tool reports.

`oob_live_entry.fatbin` is that window used deliberately: a complete entry
appended past the end of the declared container, with one extra byte of
declared size to make it live. The GPU runs it. `cuobjdump -xelf` extracts the
other entry, byte-identical to a clean build, and a scanner hashing extracted
cubins hashes the decoy. `oob_hidden_entry.fatbin` is the same file with that
one byte removed, where the appended entry is inert, which is the control.

## The walk bound is a signed 32-bit value

`trunc_negative.fatbin` declares `fatbin_size = 0x800018D0`. Read as a u64 that is
2.1 GB; read as the driver reads it, it is negative, no entry is reached, and
the load fails with `CUDA_ERROR_NO_BINARY_FOR_GPU` while a u64-reading parser
reports two live entries.

`trunc_high.fatbin` declares `0x100000C68`. The low 32 bits are 3176, so the
driver walks exactly one entry, while a u64 reader sees a container of 4 GB.
Measured, and matching the `movslq %esi,%rax` at `0x47b6f1`, which is quoted in
`decompiled-selection.md` where the walk's bound is discussed.

## What each reader sees

Every column measured. "declared hash" is
`sha256(payload[0:padded_payload_size])`,
the obvious implementation. "extent hash" is what
`scripts/fatbin_entry_selection.py` now hashes, the bytes the driver reads.

| container | the GPU ran | `cuobjdump` | declared hash | extent hash |
|---|---|---|---|---|
| `ptx_collide_a` | variant_a | nothing, then errors | `5f9458539df4` | `492802f186a8` |
| `ptx_collide_b` | variant_b | nothing, then errors | **`5f9458539df4`** | `a700990277fb` |
| `ptx_declared0` | variant_a | nothing, then errors | none, 0 bytes | `492802f186a8` |
| `hidden_cubin` | variant_a | 1 entry, clean exit | `c479f634ae82` | `69e6171ebbd6` |
| `swallow_entry` | variant_a | 1 entry, clean exit | `53620ff91eb4` | `69e6171ebbd6` |
| `swallow_bit24` | variant_a | 1 entry, clean exit | `53620ff91eb4` | `69e6171ebbd6` |
| `oob_hidden_entry` | variant_a | 1 entry, then errors | `69e6171ebbd6` | `69e6171ebbd6` |
| `oob_live_entry` | **variant_b** | 1 entry, then errors | `765aa92f36ab` | `765aa92f36ab` |
| `trunc_negative` | nothing runs | nothing, then errors | n/a | n/a |
| `trunc_high` | variant_a | nothing, then errors | `69e6171ebbd6` | `69e6171ebbd6` |

`69e6171ebbd6` is the hash of a pristine `variant_a.cubin`, so the extent-aware
hash of the hidden and swallowed containers equals the hash of the clean build,
which is the correct answer: the appendix is carried by the container and
executed by nothing.

Two observations about `cuobjdump` belong here rather than in the table, and
its non-zero exits have three different causes, none of them a check on which
entry runs.

On `oob_hidden_entry` and `oob_live_entry` it chains to the next container at
`base + 16 + fatbin_size`, finds no magic, and fails after having already printed
and extracted an entry. That is the case where a pipeline ignoring the exit
code gets a confident wrong answer, and the extracted cubin is the decoy rather
than the code that ran. On `trunc_negative` and `trunc_high` it rejects the
container up front, because the declared size exceeds the file, which is a
bounds check on `fatbin_size` that the driver does not perform. On the three PTX
containers it prints nothing at all: the intra-container walk lands on text and
gives up, so a pipeline ignoring the exit code gets an empty answer rather than
a wrong one.

The quiet cases are `hidden_cubin` and `swallow_entry` and `swallow_bit24`,
which exit **zero**. There the listing is clean and, in the narrow sense, it is
also right: the entry it extracts is the one that ran. What it conceals is that
the container carries a second cubin, or a swallowed entry, that no reader
reports. `oob_live_entry` is the only row where `cuobjdump` is wrong about what
executes.

## What a tool should do instead

- Walk with the driver's bound: truncate `fatbin_size` to a signed 32-bit value,
  and treat an entry as present when its start is inside it.
- Hash the extent the driver reads, per kind, not the declared payload.
- Report the difference rather than swallowing it. Declared bytes past the end
  of the embedded ELF, PTX text past the declared payload, an entry crossing
  the container boundary, and a non-positive truncated `fatbin_size` are all
  recorded as notes by `scripts/fatbin_entry_selection.py`, because each one is
  a place where two readers of the same file will disagree.
