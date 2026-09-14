# Step 1, Fatbin entry precedence

Measured 2026-09-10. Which entry does the CUDA driver execute when a fatbin
contains more than one entry matching the running GPU?

## Test environment

| | |
|---|---|
| GPU | NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9 |
| Driver | 595.71 (Windows host, reached through the WSL2 stub) |
| Toolkit | CUDA 13.2, nvcc V13.2.86 |
| Host | Ubuntu 22.04 on WSL2 |

Every run had `CUDA_CACHE_DISABLE=1` set, so a cached JIT result cannot be
mistaken for a fresh selection decision.

## Method

Two kernels, `variant_a` and `variant_b`, export the **same** symbol `probe`
and differ only in the marker they write, 0xAAAA and 0xBBBB. Because the symbol
is identical, `cuModuleGetFunction` succeeds either way and the marker returned
identifies which entry the driver chose.

`fatbinary` combines them into images holding two entries that both match
sm_89. It accepts duplicate architectures without complaint and preserves the
order given on the command line; the paired images below differ only in that
order.

The harness (`src/harness/loader.cpp`) loads an image, launches `probe` on four
threads, and reports the marker. The output buffer is pre-filled with 0xDEAD so
that a kernel which never ran identifies itself rather than returning a
plausible zero.

## Results

Controls first. Single-entry images returned their own marker, confirming the
harness reports what actually executed.

| Image | Entries (in order) | Executed |
|---|---|---|
| `variant_a.cubin` | ELF A | variant_a |
| `variant_b.cubin` | ELF B | variant_b |
| `elf_ab.fatbin` | ELF A, ELF B | **variant_a** |
| `elf_ba.fatbin` | ELF B, ELF A | **variant_b** |
| `ptxa_elfb.fatbin` | PTX A, ELF B | **variant_b** |
| `elfb_ptxa.fatbin` | ELF B, PTX A | **variant_b** |

`cuModuleLoadData` and `cuModuleLoadFatBinary` produced identical results on
all four conflict images. No divergence between those two entry points.

## Precedence rules

Two different rules, depending on what is in conflict.

1. **ELF against ELF, same architecture: first entry wins.** Swapping the order
   swaps the winner, so selection is positional.
2. **PTX against ELF, same architecture: ELF wins regardless of position.**
   Entry kind outranks order.

## Finding: the PTX entry is unreachable, and says so nowhere

For `ptxa_elfb.fatbin`, `cuobjdump -lelf -lptx` reports both entries. The PTX
entry contains:

```
mov.u32 %r2, 43690      // 0xAAAA, variant_a
```

The GPU executed 0xBBBB. The PTX describes code that never runs.

Nothing in the PTX marks it as dead. Distinguishing a live entry from a dead
one requires applying the driver's precedence rule, and that rule is not
documented. Any analysis that reads the PTX, the tempting choice, since PTX is
text while the alternative needs disassembly, is describing code the hardware
never executes.

To be precise about scope: `cuobjdump` is not wrong here, it lists both
entries. The gap appears in any tool or process that picks one representation
to inspect, hash, or attest, without reproducing the driver's selection logic.

## Second-order result: the live entry is environment-dependent

With `CUDA_FORCE_PTX_JIT=1`, `ptxa_elfb.fatbin` executes variant_a instead of
variant_b. The same bytes run different code depending on an environment
variable, so an analysis sandbox and a production host can disagree about what
a fatbin does, without either being misconfigured.

## Reproduction

```sh
make -C src/kernels          # cubins, ptx, and the four conflict fatbins
make -C src/harness          # the loader
cd build
export CUDA_CACHE_DISABLE=1
./loader elf_ab.fatbin
./loader ptxa_elfb.fatbin
./loader ptxa_elfb.fatbin --fatbinary
CUDA_FORCE_PTX_JIT=1 ./loader ptxa_elfb.fatbin
```

Artifact hashes, first 16 hex characters of SHA-256:

```
69e6171ebbd6b307  variant_a.cubin
765aa92f36ab096e  variant_b.cubin
bd02782646db8e9b  variant_a.ptx
a3b7a73f1338e6be  elf_ab.fatbin
407a21b506db18c8  elf_ba.fatbin
e636fbda03be6334  ptxa_elfb.fatbin
f5495b8f44cbfd5e  elfb_ptxa.fatbin
```

## Confirmed: order is positional, and the container has no integrity metadata

`elf_ab.fatbin` and `elf_ba.fatbin` differ in exactly four bytes, at two
positions:

```
0x0714:  AA AA  ->  BB BB      (first payload's marker)
0x137C:  BB BB  ->  AA AA      (second payload's marker)
```

Both sit inside an identical SASS immediate encoding, `02 78 05 00 [marker]
00 00 00 0f`.

Two conclusions.

**`fatbinary` preserves command-line order rather than sorting.** Had it sorted
entries into a canonical order, both files would place the same variant first
and be byte-identical. The driver's choice tracks which payload sits first in
the file, so selection between two ELF entries is positional.

**The container carries no per-entry integrity metadata.** Every byte outside
those two markers is identical, container and entry headers included. A
per-entry hash, checksum, or content identifier would have swapped along with
the payloads and shown up in this diff. None did. A payload can therefore be
substituted without anything at the container level detecting it, which is the
mechanism the rest of this work depends on.

Incidental measurement for the parser: the two markers are 3176 bytes apart and
each cubin is 3112 bytes, leaving 64 bytes of container per entry. A lead to
check entry-header layout against, not yet a conclusion, since padding could
account for part of it.

## Resolved: ordinary builds do NOT ship duplicate entries

A default `nvcc -arch=sm_89` build makes `cuobjdump` list two sm_89 ELF images,
which raised the question of whether duplicate same-architecture entries appear
in ordinary compiler output. They do not. Walking the container headers in
`.nv_fatbin` gives:

| Artifact | Section | Fatbins | Wrappers | Fat sizes |
|---|---|---|---|---|
| object, kernel only | 3504 B | 1 | 1 | 3488 |
| object, kernel + main | 3504 B | 1 | 1 | 3488 |
| linked executable | 5040 B | **2** | **2** | 1520, 3488 |

The two images live in **two separate fatbins**, each with its own registration
wrapper in `.nvFatBinSegment`, not as duplicate entries inside one container.

Linking is what adds the second one. Both object files carry a single 3488-byte
fatbin; the executable carries that same fatbin plus a new 1520-byte one, which
is the device-link stub the device linker emits. The header walk consumes the
section exactly, 16 + 1520 + 16 + 3488 = 5040 bytes, with nothing left over.

This is a negative result: the conflict cases in this document still have to be
constructed deliberately. They are not a naturally occurring hazard.

One related observation does survive. `cuobjdump -lelf` flattens both containers
into a single numbered list and labels both `sm_89`, with nothing indicating
they came from different fatbins. A tool consuming that output cannot recover
container boundaries, which is a smaller instance of the same
scanner-versus-driver gap this document is about.

### Method note

`grep` on this machine resolves to `ugrep`, which does not match raw byte
patterns passed as `$'\x50\xed\x55\xba'`, and silently reports zero hits even
when the magic is at offset 0. Counting magics by piping to `grep -c` is also
wrong regardless of implementation, since it counts matching *lines* and binary
data puts many magics on one line. Walk the headers with a real parser instead:
read the magic, `headerSize`, and `fatSize`, then jump by `headerSize + fatSize`.

## Open questions

*Status updated 2026-09-14. Two of these are now closed; see the note at the
end of this file.*

- ~~Only two entries tested. Does the rule hold at three or more?~~ **Closed:**
  it holds at three, in both directions.
- ~~Entry-header architecture disagreeing with the embedded ELF's
  `e_flags`.~~ **Closed:** selection uses the header, the ELF is validated
  afterwards, and the two can disagree silently.
- **Still open:** a payload hidden in `padded_payload_size` slack, and an entry
  positioned past the declared `fatbin_size`.

## Addendum: the positional rule reverses for PTX

Measured 2026-09-11, driver 597.06, same method as above.

Two PTX entries at the same architecture: the **last** in file order executes.
This is the opposite of two ELF entries, where the first wins.

| Container | Entry order | Executed |
|---|---|---|
| ELF + ELF | variant_a, variant_b | variant_a (first) |
| ELF + ELF | variant_b, variant_a | variant_b (first) |
| PTX + PTX | variant_a, variant_b | variant_b (last) |
| PTX + PTX | variant_b, variant_a | variant_a (last) |

Both directions were tested for both kinds, from the same source kernels, with
`CUDA_CACHE_DISABLE=1` throughout, so the reversal is not an artefact of build
order or caching.

The consequence for static analysis is worse than a single undocumented rule.
There is no one positional convention to implement: a tool that picks the first
matching entry is correct for cubins and incorrect for PTX, and vice versa.

A related observation, measured less exhaustively: with two PTX entries at
*different* architectures, the higher architecture won regardless of order
(compute_75 against compute_89, both orders). Only that one pair was tested.

## Addendum 2: the full hierarchy, and a flag bit that hides an entry

Measured 2026-09-11, driver 597.06. These correct and extend the addendum above.

### Selection is three levels, not one

| Level | Rule | Evidence |
|---|---|---|
| 1. kind | ELF beats PTX | sm_86 ELF beats compute_89 PTX, both orders |
| 2. architecture | nearest compatible wins, order-independent | sm_89 ELF beats sm_86 ELF, both orders |
| 3. file order | ELF: first wins. PTX: last wins | both directions tested per kind |

An sm_86 cubin loads and runs on its own on this sm_89 GPU, so it is a real
candidate rather than an invalid entry that is simply skipped. The earlier
statement that "selection is positional" was too strong: position only decides
ties at the bottom of the hierarchy.

### A flag bit removes an entry from selection invisibly

A single valid sm_89 cubin in a self-consistent container, with one bit of the
entry `flags` field (entry+0x28) changed:

| flags | Driver | cuobjdump |
|---|---|---|
| 0x11 (baseline) | executes | disassembles fully |
| bit 20 set | `CUDA_ERROR_NO_BINARY_FOR_GPU` | disassembles fully |
| bit 21 set | `CUDA_ERROR_NO_BINARY_FOR_GPU` | disassembles fully |
| bit 24 set | executes | disassembles fully |

Bits 20 and 21 remove the entry from the driver's candidate set. Bit 24 does
not. Every size and offset in the file remains correct, so no parser has any
structural reason to object, and the disassembler happily prints the SASS of
code the GPU will never run.

## Correction, 2026-09-12: flag bits 20 and 21 are architecture suffixes

Addendum 2 above describes bits 20 and 21 of the entry `flags` field as
removing an entry from the driver's candidate set, and calls the effect
invisible. The measurements in that section all still reproduce on driver
597.06, but the mechanism is not what was claimed.

The driver renders an entry's target as a name, `sm_<arch><suffix>`, and takes
the suffix from those two bits: `a` for bit 20, `f` for bit 21. They are the
suffixes `nvcc` exposes as `sm_90a` and `sm_100f`. Building one container per
target and reading the bits back confirms it: `sm_90a` sets bit 20, `sm_100f`
sets bit 21, and the plain targets set neither.

So setting bit 20 on an sm_89 entry does not hide it. It makes the entry
declare `sm_89a`, which is not a target any GPU reports, so the container has
no candidate left and the load fails. Bit 24 is not part of the encoding,
which is why it looked inert here; it appears on compute capability 100 and
above.

The consequence for tooling stands and is more precise than before.
`cuobjdump -lelf` lists the entry as `sm_89`, dropping the suffix, while
`cuobjdump -elf` reports `arch = sm_89a`. The field that decides whether the
entry can run is present in one output and absent from the other, and the one
that omits it is the listing a tool parses.

Details and the instruction-level evidence are in `driver-selection-logic.md`.


## Closing two open questions, 2026-09-14

### The rule holds at three entries

Containers with three ELF entries at sm_75, sm_80 and sm_86 were measured in
both orders on an sm_89 GPU. The sm_86 entry wins from either end, so
architecture proximity is order-independent at three entries and not just at
two. The earlier result was not an artefact of testing pairs.

### The architecture is stated twice, and the two fields can disagree

A cubin entry declares its architecture in the fat binary entry header at
`+0x1c`, and again inside the embedded ELF, in `e_flags`. Nothing makes them
agree. Patching one and leaving the other gives four measured outcomes:

| entry header | embedded ELF | result |
|---|---|---|
| sm_89 | sm_86 | runs |
| sm_86 | sm_89 | runs |
| sm_89 | sm_75 | `CUDA_ERROR_INVALID_SOURCE` |
| sm_75 | sm_89 | `CUDA_ERROR_NO_BINARY_FOR_GPU` |

The two different error codes are the finding. A header claiming an
unselectable architecture yields "no binary for GPU": the entry was never a
candidate and its payload was never read. A header claiming a selectable
architecture over a payload for the wrong generation yields "invalid source":
the entry was chosen, and only then was the ELF looked at and refused.

**Selection reads the header. Validation reads the ELF. In that order.**

Where both values are individually runnable the mismatch is tolerated in
silence, which is the first two rows. And `cuobjdump` reports the two fields
through different flags, so it contradicts itself on the same entry:

| container | `cuobjdump -lelf` | `cuobjdump -elf` |
|---|---|---|
| unmodified | sm_89 | sm_89 |
| header sm_89, ELF sm_86 | sm_86 | sm_89 |
| header sm_86, ELF sm_89 | sm_89 | sm_86 |

The entry listing reports the ELF's value; the ELF dump reports the header's.
The listing is the machine-readable output a tool is most likely to parse, and
it is the one showing the field selection does not use.

### Selection commits, and there is no fallback

Put a bad payload first and a good one second: entry 0 selectable by its header
but carrying an ELF for the wrong generation, entry 1 an ordinary sm_89 cubin.
The load fails with `CUDA_ERROR_INVALID_SOURCE`. The driver does not retry with
the next candidate. One entry is chosen and that decision is final, which is
worth stating because "best matching" suggests otherwise.

### Method note on `e_flags`

Where the SM number sits inside `e_flags` depends on the cubin ELF ABI version
in byte 8 of `e_ident`, and assuming one layout invents disagreements that are
not there. Version 8, which the CUDA 13.2 toolkit emits, puts it in bits 8 to
15, so sm_89 is `0x06005904`. Version 7, which the cubins inside NVIDIA's own
shipped libraries use, puts it in bits 16 to 23, so sm_75 is `0x004b054b`.
A first attempt at this check assumed the version 8 layout and reported three
shipped libraries as mismatched; all three were false positives. The parser now
keys on the ABI version and declines to check an unrecognised one, on the view
that a missed disagreement is better than an invented one.
