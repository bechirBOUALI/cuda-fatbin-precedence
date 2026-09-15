# The size fields decide what the driver reads, and they are not lengths

`entry-precedence.md` answers which entry the driver selects. This document
answers the question underneath it: once an entry is selected, which bytes are
that entry. The container states two sizes and neither means what a reader
would assume, so a tool can hash the right entry and still hash the wrong
bytes.

Both cases were listed as open in earlier versions of this repository. They are
settled here, by execution.

| | |
|---|---|
| GPU | NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9 |
| Driver | 597.06, reached through the WSL2 stub |
| Toolkit | CUDA 13.2 |
| Method | every container loaded with `CUDA_CACHE_DISABLE=1`, marker read back |

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
Measured, and matching the `movslq %esi,%rax` at `0x47b6f1` recorded in
`driver-selection-logic.md`.

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

## Open

The behaviour of containers whose declared sizes make the driver refuse or
misbehave, rather than merely disagree with a reader, is deliberately not
documented here. Those cases were measured and are being reported to NVIDIA
separately; nothing in this repository depends on them.
