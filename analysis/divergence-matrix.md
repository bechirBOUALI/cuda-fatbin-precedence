# The divergence matrix

Driver 597.06, RTX 2000 Ada (sm_89), CUDA 13.2, WSL2. Tables below are emitted
by `scripts/divergence_matrix.py` and `scripts/survey_libs.py`.

NVIDIA documents that a compatible cubin is preferred over PTX, so rows where
that alone decides the outcome confirm the documented rule rather than
extending it. The rows that matter here are the ones where several entries all
match and something has to rank them, which is what is not documented.

What a static reading of a fat binary reports, against what the GPU actually
ran. The driver column is ground truth: each container was loaded and the
marker its kernel wrote was read back. Every other column is a prediction made
from the bytes alone.

## The readings being compared

These three are not measurements of any shipping product. They are the
plausible ways a tool could choose an entry, written here so the driver's rule
has something to be compared against. What the comparison establishes is where
each shortcut breaks, not how any particular scanner behaves.

| How a tool picks the entry to inspect | What it does | Why a tool would do this |
|---|---|---|
| first-match | first entry whose architecture does not exceed the GPU | the obvious loop, and what a linear scan gives you |
| exact-arch | first entry whose architecture equals the GPU exactly, else first-match | the careful version, and correct on most shipped libraries |
| prefer-PTX | the first PTX entry, else first-match | PTX is text, so it needs no disassembler |
| precedence-aware | the driver's own rule | `would_execute()` in `scripts/fatbin_entry_selection.py` |

## The matrix

| container | driver runs | first-match | exact-arch | prefer-PTX | precedence-aware |
|---|---|---|---|---|---|
| control, one sm_89 ELF | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| ELF A + ELF B, both sm_89 | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| ELF B + ELF A, both sm_89 | **variant_b** | variant_b | variant_b | variant_b | variant_b |
| PTX A + PTX B, both compute_89 | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| PTX B + PTX A, both compute_89 | **variant_a** | variant_b (wrong) | variant_b (wrong) | variant_b (wrong) | variant_a |
| sm_89 ELF A + sm_86 ELF B | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| sm_86 ELF B + sm_89 ELF A | **variant_a** | variant_b (wrong) | variant_a | variant_b (wrong) | variant_a |
| sm_80 ELF A + sm_86 ELF B, neither exact | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| sm_86 ELF B + sm_80 ELF A, neither exact | **variant_b** | variant_b | variant_b | variant_b | variant_b |
| sm_75 + sm_80 + sm_86 ELF, ascending | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| sm_86 + sm_80 + sm_75 ELF, descending | **variant_b** | variant_b | variant_b | variant_b | variant_b |
| control, one sm_75 ELF, wrong generation | **nothing runs** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | nothing runs |
| sm_75 ELF A + compute_75 PTX B | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_b | variant_b |
| compute_75 PTX B + sm_75 ELF A | **variant_b** | variant_b | variant_b | variant_b | variant_b |
| compute_89 PTX A + sm_89 ELF B | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| sm_89 ELF B + compute_89 PTX A | **variant_b** | variant_b | variant_b | variant_a (wrong) | variant_b |
| sm_86 ELF A + compute_89 PTX B | **variant_a** | variant_a | variant_b (wrong) | variant_b (wrong) | variant_a |
| compute_89 PTX B + sm_86 ELF A | **variant_a** | variant_b (wrong) | variant_b (wrong) | variant_b (wrong) | variant_a |
| header sm_89, ELF e_flags sm_86 | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| header sm_86, ELF e_flags sm_89 | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| header sm_89, ELF e_flags sm_75 | **rejected at load** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | rejected at load |
| header sm_75, ELF e_flags sm_89 | **nothing runs** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | nothing runs |
| bad-payload A then good B, no fallback | **rejected at load** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | rejected at load |
| one sm_89 ELF, flags bit 20 set | **nothing runs** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | nothing runs |
| one sm_89 ELF, flags bit 21 set | **nothing runs** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | nothing runs |
| one sm_89 ELF, flags bit 24 set | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| ELF A + ELF B, bit 24 on A only | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| ELF A + ELF B, bit 24 on B only | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| PTX, 200 of 344 bytes declared | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| same 200 declared bytes, other kernel | **variant_b** | variant_b | variant_b | variant_b | variant_b |
| PTX, 0 bytes declared | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| second cubin inside the declared payload | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| entry 0 declares away entry 1 | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| same, over the bit 24 tie-break | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| entry appended past the declared container | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| same, one byte more declared | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| fat_size 3176, entry 1 outside | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| fat_size 3177, entry 1 starts inside | **variant_b** | variant_a (wrong) | variant_a (wrong) | variant_a (wrong) | variant_b |
| fat_size 0x800018D0, negative as int32 | **nothing runs** | nothing runs | nothing runs | nothing runs | nothing runs |
| fat_size 0x100000C68, low 32 bits used | **variant_a** | variant_a | variant_a | variant_a | variant_a |
| PTX A + ELF B, CUDA_FORCE_PTX_JIT=1 | **variant_a** | variant_a | variant_a | variant_a | variant_a |

41 cases over 40 containers, each one measured on the GPU.
rows where the reading disagrees with what executed:
  first-match        17 / 41
  exact-arch         17 / 41
  prefer-PTX         18 / 41
  precedence-aware    0 / 41

Reproduce with:

```sh
make -C src/kernels && make -C src/harness
python3 scripts/divergence_matrix.py
```

## What each block of rows establishes

**Rows 1 to 3, controls.** One entry, then two ELF entries at the same
architecture in both orders. Every reading agrees with the driver, so the
disagreements below are not artefacts of the harness.

**Rows 4 and 5, the positional rule reverses.** Two PTX entries at compute_89:
the last one runs. Two ELF entries at sm_89, rows 2 and 3: the first one runs.
No single positional convention is correct for both kinds.

**Rows 6 to 11, architecture outranks order.** An sm_89 against an sm_86 cubin,
then sm_80 against sm_86 where neither matches exactly, then three entries at
sm_75, sm_80 and sm_86. The nearest compatible entry wins from either end.
Rows 10 and 11 answer the question the paired cases leave open: the rule holds
at three entries too, so it is not an artefact of testing pairs.

**Rows 12 to 14, compatibility is a filter and not a preference.** A lone sm_75
cubin is refused with `CUDA_ERROR_NO_BINARY_FOR_GPU`, because cubins are
compatible only inside their major generation. Rows 13 and 14 then put that
unrunnable cubin against a compute_75 PTX entry, and the PTX wins in both
orders even though kind preference ranks ELF above PTX. Incompatible entries
leave the candidate set before ranking begins, so the hierarchy operates only
on entries that could actually run.

That also makes rows 10 and 13 sharper than they look. sm_75 is the entry a
first-match reading selects there, and in most of the shipped libraries below.
A conventional reading is not picking a suboptimal entry in those cases, it is
naming one the GPU refuses.

**Rows 15 to 18, kind outranks both.** Among compatible entries an ELF beats a
PTX in both orders, and an sm_86 ELF beats an exactly matching compute_89 PTX.
Row 17 is what breaks exact-arch: the entry whose architecture matches
perfectly is the one that does not run.

**Rows 19 to 23, the architecture is stated twice.** A cubin declares its
architecture in the entry header and again in the embedded ELF's `e_flags`,
and nothing makes them agree. Selection reads the header and validation reads
the ELF, in that order, which the two distinct error codes prove: an
unselectable header gives "no binary for GPU" and the payload is never read,
while a selectable header over a wrong-generation payload gives "invalid
source". Where both values are individually runnable the mismatch passes in
silence. Row 23 adds that selection commits: a rejected payload does not fall
back to the good entry sitting right after it.

`cuobjdump` reports the two fields through different flags and so contradicts
itself on the same entry, with `-lelf` showing the ELF value and `-elf` the
header value. The listing, which is what a tool parses, is the one showing the
field selection does not use.

**Rows 24 to 28, the flags field decides two different things.** A
structurally perfect sm_89 cubin with a single bit of the entry `flags` field
set. Bits 20 and 21 are the architecture-name suffix, `a` and `f`, the same
ones `nvcc` exposes as `sm_90a` and `sm_100f`, so setting bit 20 here makes the
entry declare `sm_89a`. No GPU reports that target, the container is left with
no candidate, and the load fails. Bit 24 is not part of the encoding, which is
why it changes nothing on an sm_89 entry.

The reading error is in the name. `cuobjdump -lelf` lists the entry as `sm_89`
and disassembles it fully, while `cuobjdump -elf` reports `arch = sm_89a`. The
suffix that decides whether the entry can run is missing from the listing a
tool parses, so all three conventional readings report a kernel that cannot
run. The parser prints the suffix in its architecture column and accepts
`--target sm_89a` to model a host that would ask for it.

Rows 27 and 28 are a different bit and a different mechanism. Bit 24 separates
two cubins that tie on kind and architecture, and the entry **without** it
wins. Two sm_89 cubins in the order A then B normally give A, because it is
first; setting bit 24 on A alone gives B instead, and setting it on B alone
changes nothing. That was predicted from the ranker's decompiled tail and then
measured, and it means one bit reverses which of two same-architecture kernels
executes while every size, offset, magic and architecture field stays correct.
Row 26 shows why the single-entry test had looked inert: with nothing to tie
against, the tie-break never runs.

**Rows 29 to 40, the size fields.** These ask a different question from every
row above: not which entry is selected, but which bytes are that entry. Two
containers declaring byte-identical payloads run different kernels, a PTX entry
declaring no payload at all runs a full kernel, a second cubin sits unread
inside a widened declared payload, an entry appended past the declared
container executes, and a `fat_size` with bit 31 set leaves the driver walking
nothing.

The three conventional columns are uninformative on these rows, and the reason
is worth stating rather than hiding: they are fed this parser's entry list, so
they inherit its corrected bounds and extents and can only disagree about
selection. A real tool reading the file with its own bounds gets these wrong in
a way this table cannot express. The comparison that does express it, against
`cuobjdump` and against a declared-size hash, is in `size-fields.md`.

**Row 41, the host decides too.** The same bytes as row 15, on a host with
`CUDA_FORCE_PTX_JIT=1`. The PTX entry runs instead of the ELF. No reading of
the file alone can be right for both hosts, so a precedence-aware parser has to
take the host policy as an input, which `would_execute()` does.

## The same question on libraries nobody crafted

The matrix is built from containers made to conflict, which invites the
objection that the conflicts are artificial. So here is the same comparison
against every fat binary in the CUDA toolkit's own shipped libraries, none of
which were built here:

```
library                            cont multi first-match  exact-arch  prefer-PTX  notes
libcublas.so.13.4.1.3               193   192         192         192         192      0
libcufftw.so.12.2.0.57                1     0           1           1           1      0
libcufile.so.1.17.1                   1     1           1           0           1      0
libnppc.so.13.1.0.59                  1     0           1           1           1      0
libnppial.so.13.1.0.59               11    11          11           0          11      0
libnppicc.so.13.1.0.59               13    13          13           0          13      0
libnppidei.so.13.1.0.59              26    26          26           0          26      0
libnppig.so.13.1.0.59                15    15          15           0          15      0
libnppim.so.13.1.0.59                12    12          12           0          12      0
libnppist.so.13.1.0.59               24    24          24           0          24      0
libnppisu.so.13.1.0.59                1     0           1           1           1      0
libnppitc.so.13.1.0.59                3     3           3           0           3      0
libnpps.so.13.1.0.59                 31    31          31           0          31      0
libnvjpeg.so.13.1.0.59               11    11          11           0          11      0
TOTAL, 14 libraries                 343   339         342         195         342      0
```

Reproduce with `python3 scripts/survey_libs.py`.

**The divergence does not need an attacker.** 339 of 343 shipped containers
hold more than one entry, and first-match names the wrong entry in 342 of them.
NVIDIA ships one entry per architecture in ascending order, so first-match
lands on the lowest, which here is usually sm_75, shown in row 12 to be
unrunnable on this GPU. The four single-entry containers do not rescue it:
three hold one sm_75 cubin and nothing else, so on this GPU they carry no
runnable code at all, and every reading still names an entry that cannot
execute.

**exact-arch is right until it is not.** It reads the correct entry in 148 of
the 343, and 192 of its 195 failures are in cuBLAS, which ships no sm_89 cubin
at all: its containers carry sm_75, sm_80, sm_86, sm_90, sm_100 and sm_120, so
there is no exact match to find and it degrades to first-match. A rule that is
correct on benign input and wrong on the rest is the least useful kind of rule
to put in a scanner.

**And it does not need a multi-architecture build either.** An ordinary
`nvcc -arch=sm_89 -c` with no other flags emits one container holding an
sm_89 cubin and a compute_89 PTX entry. The cubin wins, so the PTX in the
default output of a default compile is already unreachable, and `cuobjdump`
lists both without distinction. With no `-arch` at all the toolkit targets
sm_75, and on this GPU that flips: the sm_75 cubin is the wrong generation, so
the PTX is the only candidate and the driver JITs it, while first-match and
exact-arch both report the cubin.

342 of the 343 containers parsed with no structural complaint, so the parser is
exercised on real shipped code and not only on its own corpus. The exception is
one PTX entry in `libcufile` carrying flags `0x2011`, which is bit 13 rather
than the zstd bit 15. Stealthium's published `BinInfo` enum names bit 13
`LZ4Compression`, alongside `ZLIBCompression` at bit 12 and a second LZ4 variant
at bit 14, so the field carries a compression family and not a single bit. This
parser decodes only zstd, and says so rather than hashing the stored bytes as
though they were code.

## A separate question: does anything validate payloads

Selection decides which entry runs. Nothing above says whether the bytes of
that entry are checked. Substituting two bytes inside the compiled SASS of one
valid entry, leaving every size, offset and magic correct:

```
make -C src/kernels tamper
CUDA_CACHE_DISABLE=1 ./build/loader build/tampered_payload.fatbin
marker  : 0xCCCC -> unrecognised
```

The tampered kernel loads and runs, so neither the container nor the driver
validates entry contents. This is kept out of the matrix because it asks about
integrity rather than precedence, but it is what makes the precedence gap worth
caring about: an entry can be substituted as well as mis-selected.

## Scope

The driver column is one GPU and one driver, 597.06 on sm_89, and the driver
reverse engineering in `driver-selection-logic.md` was done on that same build,
so both halves agree on version. The rules are measured and explained on one
configuration; they are expected to hold more broadly, but that is an
expectation. `would_execute()` takes the target architecture and suffix as
arguments, so the same corpus can be re-run elsewhere.
