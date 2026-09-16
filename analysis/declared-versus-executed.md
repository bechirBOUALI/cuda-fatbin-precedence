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

## Field names, against Stealthium's published struct

The format was documented publicly before this work, so the names here are
mapped to that account rather than invented alongside it. Everything below is
the same byte at the same offset under two names.

Container header:

| offset | here | Stealthium |
|---|---|---|
| 0x00 | `magic` | `magic` |
| 0x04 | `version` | `version` |
| 0x06 | `header_size` | `header_size` |
| 0x08 | `fat_size` | `fatbin_size` |

Entry header:

| offset | here | Stealthium |
|---|---|---|
| 0x00 | `kind` | `kind` |
| 0x04 | `header_size` | `header_size` |
| 0x08 | `payload_size` | `padded_payload_size` |
| 0x10 | `compressed_size` | `payload_size` |
| 0x14 | `opts_desc_offset` | `ptxas_options_offset` |
| 0x18, 0x1a | `version_minor`, `version_major` | `code_version_minor`, `code_version_major` |
| 0x1c | `arch` | `arch` |
| 0x20 | `ident_offset` | `identifier_offset` |
| 0x24 | `ident_length` | `field_24`, undocumented |
| 0x28 | `flags` | `bin_info` |
| 0x30 | `obfuscation_key` | `field_30`, undocumented |
| 0x38 | `decompressed_size` | `uncompressed_payload` |

Two of those are identifications rather than renamings. `field_24` is the
length of the identifier string at `identifier_offset`: a container built with
`fatbinary --ident=IDENTMARKER99` carries 13 there. `field_30` holds the
obfuscation key, the decimal digits of `fatbinary --okey` read back as hex,
which `ptx-obfuscation.md` measures.

The naming matters for the finding below, because the two accounts split the
size fields differently. What advances the walk, and what this document is
about, is the u64 at 0x08: `payload_size` here, `padded_payload_size` there.
The i32 at 0x10 is 0 for an uncompressed entry, so for those entries the u64 is
the only length a reader has. Where the text below says an entry declares 200
bytes, that is the field at 0x08 under either name.

One name is worth questioning rather than mapping. `fatbin_size` is described
as the total size of the contained binaries, which is the natural reading and
the one a parser implements. The driver does not treat it that way: it
truncates the value to a signed 32-bit quantity and uses it only to bound how
far the walk advances, so an entry whose start falls inside it is walked even
when its body lies far outside, and a declared size that covers nothing at all
is still a valid container to the driver.

## The two rules

**`payload_size` advances the walk. It does not bound the read.**

| Kind | What the driver reads |
|---|---|
| PTX | from the payload start to the first NUL, however long `payload_size` says |
| ELF | the extent the embedded ELF's own headers describe, which can be longer or shorter than `payload_size` |

**`fat_size` bounds the walk, twice removed from what it says.** The driver
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

A tool hashing `payload[0 : payload_size]` gives both files the same hash and
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
`0xBBBB` to `0xAAAA` on a two-byte edit at offset 0x18.

## Entries outside the declared container

The boundary is exact and one byte wide. On a container whose entry 1 wins:

| declared `fat_size` | driver | `cuobjdump -lelf` |
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

`trunc_negative.fatbin` declares `fat_size = 0x800018D0`. Read as a u64 that is
2.1 GB; read as the driver reads it, it is negative, no entry is reached, and
the load fails with `CUDA_ERROR_NO_BINARY_FOR_GPU` while a u64-reading parser
reports two live entries.

`trunc_high.fatbin` declares `0x100000C68`. The low 32 bits are 3176, so the
driver walks exactly one entry, while a u64 reader sees a container of 4 GB.
Measured, and matching the `movslq %esi,%rax` at `0x47b6f1`, which is quoted in
`decompiled-selection.md` where the walk's bound is discussed.

## What each reader sees

Every column measured. "declared hash" is `sha256(payload[0:payload_size])`,
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

Two observations about `cuobjdump` belong here rather than in the table.
Against a raw `.fatbin` it exits non-zero on most of these, not from a bounds
check but because it chains to the next container at `base + 16 + fat_size` and
finds no magic there. That is an incidental tripwire, and it is partial: it
still prints and extracts the decoy entry before failing, so a pipeline that
ignores the exit code gets a confident wrong answer, while one that checks it
fails closed. The `hidden_cubin` and `swallow_*` cases produce **no error at
all**, so on those the listing is clean and wrong.

## What a tool should do instead

- Walk with the driver's bound: truncate `fat_size` to a signed 32-bit value,
  and treat an entry as present when its start is inside it.
- Hash the extent the driver reads, per kind, not the declared payload.
- Report the difference rather than swallowing it. Declared bytes past the end
  of the embedded ELF, PTX text past the declared payload, an entry crossing
  the container boundary, and a non-positive truncated `fat_size` are all
  recorded as notes by `scripts/fatbin_entry_selection.py`, because each one is
  a place where two readers of the same file will disagree.
