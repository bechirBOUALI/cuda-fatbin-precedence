# Entry precedence: which entry the driver executes

When a fat binary holds more than one entry matching the running GPU, the
driver runs exactly one. This document states the rule that decides which, as
measured by execution.

| | |
|---|---|
| GPU | NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9 |
| Driver | 597.06, reached through the WSL2 stub |
| Toolkit | CUDA 13.2, nvcc V13.2.86 |
| Host | Ubuntu 22.04 on WSL2 |

Every run sets `CUDA_CACHE_DISABLE=1`, so a cached JIT result cannot be
mistaken for a fresh selection decision.

## Method

Two kernels, `variant_a` and `variant_b`, export the **same** symbol `probe`
and differ only in the marker they write, 0xAAAA and 0xBBBB. Because the symbol
is identical, `cuModuleGetFunction` succeeds either way and the marker returned
identifies which entry the driver chose.

`fatbinary` combines them into containers holding entries that conflict. It
accepts duplicate architectures without complaint and preserves the order given
on the command line, so paired containers differ only in that order.

The harness at `src/harness/loader.cpp` loads a container, launches `probe` on
four threads and reports the marker. The output buffer is pre-filled with
0xDEAD so a kernel that never ran identifies itself rather than returning a
plausible zero.

`cuModuleLoadData` and `cuModuleLoadFatBinary` produce identical results on
every conflict container tested. There is no divergence between those two
entry points.

## The rule

Selection is a hierarchy. Each level is consulted only when the one above it
ties.

| Level | Rule |
|---|---|
| 0. compatibility | entries the GPU cannot run leave the candidate set entirely |
| 1. kind | ELF beats 0x10 beats PTX, unconditionally |
| 2. architecture | among entries of one kind, the nearest compatible one wins, independent of file order |
| 3. flag bit 24 | among ELF entries still tied, the one **without** bit 24 wins |
| 4. file order | ELF: the first wins. PTX: the **last** wins |

Levels 0 and 1 restate what NVIDIA documents, and the measurements confirm
them. Everything below is not written down anywhere.

### Level 0, compatibility is a filter and not a preference

Cubins are binary compatible only inside their major generation, so an sm_75
cubin is not a candidate on an sm_89 GPU: a container holding only that entry
is refused with `CUDA_ERROR_NO_BINARY_FOR_GPU`.

That filter runs before the kind preference, which makes it load-bearing rather
than cosmetic. Put the same unrunnable sm_75 cubin beside a compute_75 PTX
entry and the PTX executes, in either order, even though kind preference ranks
ELF above PTX. The hierarchy only ever operates on entries that could actually
run.

### Level 1, kind decides before architecture

An ELF entry beats a PTX entry in both orders. It does so even when the PTX is
an exact architecture match and the ELF is not: an sm_86 cubin beats a
compute_89 PTX on an sm_89 GPU.

**So a PTX entry is unreachable whenever a compatible cubin is present, and
nothing in the PTX says so.** `cuobjdump -lelf -lptx` lists both. For
`ptxa_elfb.fatbin` the PTX entry contains `mov.u32 %r2, 43690`, which is
0xAAAA, and the GPU executed 0xBBBB. The PTX describes code that never runs.

To be precise about scope, `cuobjdump` is not wrong here: it lists both
entries. The gap appears in any tool or process that picks one representation
to inspect, hash or attest without reproducing the driver's selection logic.

### Level 2, architecture proximity, order-independent

Among entries of one kind the nearest compatible architecture wins regardless
of position. An sm_89 cubin beats an sm_86 cubin from either end, and the sm_86
cubin runs perfectly well on its own, so it is a genuine candidate rather than
an invalid entry being skipped.

The same holds when neither entry matches exactly, which is the shape real
libraries ship: sm_80 against sm_86 gives sm_86 from either end. It also holds
at three entries, sm_75 against sm_80 against sm_86, so the rule is not an
artefact of testing pairs.

### Level 3, flag bit 24

Two cubins that tie on kind and architecture are separated by bit 24 of the
entry `flags` field, and the entry **without** it wins.

| Container | Executed |
|---|---|
| ELF A then ELF B, both sm_89 | variant_a |
| same, bit 24 set on A | **variant_b** |
| same, bit 24 set on B | variant_a |

One bit reverses which of two same-architecture kernels executes while every
size, offset, magic and architecture field stays correct. The toolkit sets this
bit on targets of compute capability 100 and above, so it is ordinary metadata
rather than a reserved field. This level was read out of the ranker's
decompiled tail and then confirmed by execution.

### Level 4, file order, and its direction reverses

| Same kind, same architecture | Winner |
|---|---|
| ELF + ELF | **first** in file order |
| PTX + PTX | **last** in file order |

Both directions were tested for both kinds from the same source kernels. There
is no single positional convention to implement: a tool that picks the first
matching entry is correct for cubins and incorrect for PTX, and the reverse
rule is wrong the other way round.

## The architecture is declared twice

A cubin entry states its architecture in the fat binary entry header at
`+0x1c`, and again inside the embedded ELF in `e_flags`. Nothing makes the two
agree.

| Entry header | Embedded ELF | Result |
|---|---|---|
| sm_89 | sm_86 | runs |
| sm_86 | sm_89 | runs |
| sm_89 | sm_75 | `CUDA_ERROR_INVALID_SOURCE` |
| sm_75 | sm_89 | `CUDA_ERROR_NO_BINARY_FOR_GPU` |

The two error codes are the finding. A header claiming an unselectable
architecture yields "no binary for GPU": the entry was never a candidate and
its payload was never read. A header claiming a selectable architecture over a
payload for the wrong generation yields "invalid source": the entry was chosen,
and only then was the ELF looked at and refused.

**Selection reads the header. Validation reads the ELF. In that order.**

Where both values are individually runnable the mismatch passes in silence, and
`cuobjdump` reports the two fields through different flags, so it contradicts
itself on the same entry:

| Container | `cuobjdump -lelf` | `cuobjdump -elf` |
|---|---|---|
| unmodified | sm_89 | sm_89 |
| header sm_89, ELF sm_86 | sm_86 | sm_89 |
| header sm_86, ELF sm_89 | sm_89 | sm_86 |

The entry listing reports the ELF's value; the ELF dump reports the header's.
The listing is the machine-readable output a tool is most likely to parse, and
it is the one showing the field selection does not use.

Where the SM number sits inside `e_flags` depends on the cubin ELF ABI version
in byte 8 of `e_ident`, and assuming one layout invents disagreements that are
not there. Version 8, which the CUDA 13.2 toolkit emits, puts it in bits 8 to
15, so sm_89 is `0x06005904`. Version 7, which the cubins inside NVIDIA's own
shipped libraries use, puts it in bits 16 to 23, so sm_75 is `0x004b054b`. The
parser keys on the ABI version and declines to check an unrecognised one, on
the view that a missed disagreement is better than an invented one.

## Selection commits, and there is no fallback

Put a bad payload first and a good one second: entry 0 selectable by its header
but carrying an ELF for the wrong generation, entry 1 an ordinary sm_89 cubin.
The load fails with `CUDA_ERROR_INVALID_SOURCE`. The driver does not retry with
the next candidate. One entry is chosen and that decision is final, which is
worth stating because "best matching" suggests otherwise.

## The live entry is environment-dependent

With `CUDA_FORCE_PTX_JIT=1`, `ptxa_elfb.fatbin` executes variant_a instead of
variant_b. The same bytes run different code depending on an environment
variable, so an analysis sandbox and a production host can disagree about what
a fat binary does without either being misconfigured.

In the driver this is not a demotion but a different filter: the variable sets
a policy field, the filter dispatches on it through a jump table, and under
that policy cubin entries are discarded before ranking happens. The correct
static answer is therefore a function of the file and the host together.

## The container carries no integrity metadata

`elf_ab.fatbin` and `elf_ba.fatbin` differ in exactly four bytes, at two
positions:

```
0x0714:  AA AA  ->  BB BB      (first payload's marker)
0x137C:  BB BB  ->  AA AA      (second payload's marker)
```

Both sit inside an identical SASS immediate encoding, `02 78 05 00 [marker]
00 00 00 0f`.

Two conclusions follow. `fatbinary` preserves command-line order rather than
sorting, since a canonical order would have made both files byte-identical. And
the container carries no per-entry integrity metadata: every byte outside those
two markers is identical, headers included, and a per-entry hash or content
identifier would have swapped along with the payloads and shown up in the diff.

The driver computes none at load either, which is measurable directly.
Substituting two bytes inside the compiled SASS of a valid entry, leaving every
size, offset and magic correct, produces a container that loads and runs the
altered kernel:

```
make -C src/kernels tamper
CUDA_CACHE_DISABLE=1 ./build/loader build/tampered_payload.fatbin
marker  : 0xCCCC -> unrecognised
```

A payload can therefore be substituted without anything at either layer
detecting it.

## Ordinary builds do not ship duplicate entries

A default `nvcc -arch=sm_89` build makes `cuobjdump` list two sm_89 ELF images,
which raises the question of whether duplicate same-architecture entries appear
in ordinary compiler output. They do not. Walking the container headers in
`.nv_fatbin` gives:

| Artifact | Section | Fatbins | Wrappers | Fat sizes |
|---|---|---|---|---|
| object, kernel only | 3504 B | 1 | 1 | 3488 |
| object, kernel + main | 3504 B | 1 | 1 | 3488 |
| linked executable | 5040 B | **2** | **2** | 1520, 3488 |

The two images live in two separate fat binaries, each with its own
registration wrapper in `.nvFatBinSegment`, not as duplicate entries inside one
container. Linking is what adds the second one, the device-link stub. The
header walk consumes the section exactly, 16 + 1520 + 16 + 3488 = 5040 bytes.

So the conflict cases in this document have to be constructed deliberately.
They are not a naturally occurring hazard. NVIDIA's own creation library agrees:
`nvFatbin.h` states that a unique identifier "is enforced as only one entry per
sm of each unique identifier".

One related observation survives. `cuobjdump -lelf` flattens both containers
into a single numbered list and labels both `sm_89`, with nothing indicating
they came from different fat binaries. A tool consuming that output cannot
recover container boundaries, which is a smaller instance of the same gap this
document is about.

## Method note on counting containers

`grep` on this machine resolves to `ugrep`, which does not match raw byte
patterns passed as `$'\x50\xed\x55\xba'` and silently reports zero hits even
when the magic is at offset 0. Counting magics by piping to `grep -c` is also
wrong regardless of implementation, since it counts matching *lines* and binary
data puts many magics on one line. Walk the headers with a real parser instead:
read the magic, `headerSize` and `fatSize`, then jump by `headerSize + fatSize`.

## Reproduction

```sh
make -C src/kernels          # cubins, PTX, and the conflict containers
make -C src/harness          # the loader
cd build
export CUDA_CACHE_DISABLE=1
./loader elf_ab.fatbin       # first ELF wins
./loader ptx_ab.fatbin       # last PTX wins
./loader ptxa_elfb.fatbin    # ELF wins over PTX
CUDA_FORCE_PTX_JIT=1 ./loader ptxa_elfb.fatbin   # now PTX wins
```

The full corpus, measured against the GPU row by row, is in
`divergence-matrix.md`. The driver code behind these rules is in
`driver-selection-logic.md` and `decompiled-selection.md`.

## Settled elsewhere

Two conflict cases were open in earlier versions of this document: a payload
hidden in the slack when the declared payload size exceeds the real one, and an
entry positioned past the declared container size. Both are measured in
`size-fields.md`. The short answer is that neither size field is a length:
`payload_size` advances the walk without bounding the read, so two containers
declaring byte-identical payloads run different kernels, and `fat_size` is
truncated to a signed 32-bit value and only has to contain an entry's first
byte, so an entry lying outside the declared container executes.
