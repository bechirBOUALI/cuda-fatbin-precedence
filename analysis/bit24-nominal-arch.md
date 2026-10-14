# Bit 24: what sets it, what reads it, and why it loses

`entry-precedence.md` established that bit 24 of `bin_info` breaks a tie between
two otherwise equal cubins, and that the entry **without** it wins.
`driver-selection-logic.md` left two things open: why the bit loses rather than
wins, and what the bit is for at all. This note answers the first and gives the
best current reading of the second.

Three independent passes produced it: the toolkit side, by building containers
and patching ELF headers; the corpus side, by counting the bit across shipped
code; and the driver side, by searching the image for every reader of the bit
rather than only the one in the ranker.

## What sets it: a copy of ELF `e_flags` bit 1

`fatbinary` does not look at the architecture at all. It copies bit 1
(`0x02`) of the cubin's own ELF `e_flags` into `bin_info` bit 24.

Established by patching `e_flags` directly and repackaging, because `fatbinary`
validates the declared `sm=` against the ELF's arch byte and so refuses the
simpler test:

| cubin | `e_flags` | declared `sm=` | resulting `bin_info` | bit 24 |
|---|---|---|---|---|
| sm_90 code, arch byte -> 100, low byte `0x04` | `0x06006404` | 100 | `0x11` | 0 |
| sm_90 code, arch byte -> 100, low byte `0x02` | `0x06006402` | 100 | `0x1000011` | **1** |
| sm_100 code, arch byte -> 90, low byte `0x02` | `0x06005a02` | 90 | `0x1000011` | **1** |
| sm_100 code, arch byte -> 90, low byte `0x04` | `0x06005a04` | 90 | `0x11` | 0 |

So the architecture number is never consulted, and the capsule sections are not
consulted either. An exhaustive single-bit sweep of `e_flags`, each value
packaged as `sm=90`, confirms it: only bit 1 changes `bin_info`, and it changes
only bit 24. Bits 16 to 23 are rejected by `fatbinary` with
`nvFatbin error: fatbinary elf mismatch`, that byte being `EF_CUDA_VIRTUAL_SM`.

The same behaviour comes from `libnvfatbin` directly, through
`nvFatbinAddCubin`, so the copy lives in the shared packaging path rather than
in the CLI.

`ptxas` is what sets `e_flags` bit 1, and it sets it for every target from
compute capability 100 upward. No compile option moves it at a fixed
architecture: `-G`, `-lineinfo`, `-rdc=true`, `-dlto`, `-Xptxas -O0/-O3`,
`-Xfatbin -compress=false`, `--fatbin` against `--cubin` and cross-virtual-arch
`-gencode` were all tried, and other bits moved while bit 24 did not.

Toolkit: CUDA 13.2 only. Whether 12.8 or 12.9 set the bit the same way is
untested, and no older toolkit on this machine ships cc >= 100 cubins.

## What it co-occurs with: capsule Mercury

Every cc >= 100 cubin from this toolkit carries an embedded capsule-Mercury
image: sections `.nv.capmerc.text.<kernel>` (type `CUDA_CAPMERC`),
`.nv.merc.symtab`, `.nv.merc.nv.info`, `.nv.merc.debug_frame` and friends.
`ptxas`'s own embedded finalizer help says so:

```
Specify the type of target ELF binary kind. Default on sm100+ is capmerc
<mercury|capmerc|sass>
Specify the opportunistic finalization level. 0=default, 1=no opportunistic
  finalization, 2=intra family finalization only, or 3=intra and inter family
  finalization
```

alongside `EIATTR_MERCURY_ISA_VERSION`, `EIATTR_MERCURY_FINALIZER_OPTIONS`,
`EICOMPAT_ATTR_ENABLE_OPPORTUNISTIC_FINALIZATION` and
`EICOMPAT_ATTR_CAN_FASTPATH_FINALIZE`.

This is the limit of what was established on the producer side. `--binary-kind`
and `-cap-merc` are not reachable from `ptxas`'s command line, so a cc >= 100
cubin without capmerc, or a cc <= 90 cubin with it, could not be built. On this
toolkit "`e_flags` bit 1 iff capmerc" and "iff cc >= 100" are the same
predicate and cannot be separated.

## What carries it in shipped code

Counted with this repository's parser over four corpora: the CUDA 13.2 toolkit
(3,516 containers), 12 GPU wheels, 37 third-party Hugging Face kernel builds,
and two toolkit static archives. 12,658 containers, 45,137 entries.

**Bit 24 is set if and only if the entry is kind 2 and its header `arch` field
is >= 100.** No exceptions in either direction. No PTX entry carries it,
including 2,457 entries at `compute_100` and above. Architectures 101, 103, 110
and 121 all carry it, so the boundary is a numeric test rather than an
enumeration of 100 and 120. It is independent of the suffix bits (5,045
`sm_90a` entries without it) and of the compression bits (it occurs with raw,
LZ4 and zstd).

Provenance, because it matters: only the toolkit corpus is the one used
elsewhere in this repository. The wheel and kernel corpora are a larger, newer
set held outside this repository, and the seven-wheel corpus behind
`ml-wheels.md` no longer exists on this machine. These counts are therefore not
a re-run of that survey and should not be mixed with its 4,641 figure.

A consequence for the selection rule: because bit 24 is a deterministic
function of the architecture, no honestly built container can hold two
same-architecture cubins that disagree on it. Level 3 cannot fire on toolkit
output. None of the 12,658 containers holds such a pair.

## What reads it: three sites, not one

Searched by scanning the whole image for every test of `0x1000000` against the
entry flags, and for byte-level tests at `+0x2b`, which a mask-only search
misses. Nothing in the image writes the bit.

Addresses are `libcuda.so.1.1` 597.06, as everywhere else in these notes. Two
callees are named by what they do rather than by an address.

**1. The filter, per-entry admission, `0x474e90`.** Read immediately after the
kind dispatch and only for kind 2:

```
474c61: je     474e90              ; kind 2 ELF -> bit 24 test
474e90: testb  $0x1,0x2b(%rbx)     ; entry flags bit 24
474e94: jne    474db0              ; SET -> the SAME path as kind 0x10
```

The path it is sent down compares the entry's architecture against the
requested one and, when they differ, calls a finalizability predicate whose
first act is `getenv("CAN_FINALIZE_DEBUG")`. That predicate special-cases
architectures `0x65`, `0x68` and `0x82`, canonicalises them to `0x6e`, `0x78`
and `0x6b`, and hard-rejects `arch <= 0x59`. So an entry carrying bit 24 is
admitted on a finalizability test rather than on a target-name match, and it
takes the branch belonging to the unnamed kind `0x10`.

**2. The ranker tail, `0x4752ec`.** The tie-break already documented in
`decompiled-selection.md`. Confirmed at instruction level in both directions.

**3. The commit path, after selection, `0x47bbda`.** Reached once an entry has
won. If the winner carries bit 24 and its architecture differs from the
device's, the driver either relabels the recorded kind from 2 to `0x10`
(`movl $0x10,0x70(%rax)`), or copies the payload out of the container and calls
a routine that rewrites the cubin's own ELF `e_flags` architecture to the
running device:

```
; inside that routine, which sits outside the range where the shift was checked
shl    $0x8,%r12d          ; requested arch << 8
and    $0xff0000ff,%esi    ; clear e_flags bits 8..23
mov    %r12d,0x30(%rbx)    ; write e_flags back
```

Its own log strings name it: "Changing architecture of non-elf candidates",
"Changing architecture of 32 bit candidates", "Changing architecture of
malformed elf candidate". An entry without bit 24 reaches none of this and
loads exactly as it sits in the container.

Nothing else in the 25 MB image reads the bit. The PTX-JIT path, the module
cache, the LTO and Tile IR filter paths and every error path were checked.

## Why it loses

Across the three sites, an entry carrying bit 24 is admitted on
finalizability rather than on a name match, ranked below an otherwise identical
entry without the bit, and, if it still wins, re-stamped or relabelled before it
can load. An entry without the bit is admitted on a name match and loaded
unmodified.

Reading, offered as inference: the bit marks a cubin whose declared
architecture is **nominal**, one the driver is prepared to retarget onto another
architecture in its family. The polarity is then not a penalty but a preference
for the candidate that needs no work before it runs, which is the same ordering
the kind cascade already applies when it puts ELF above kind `0x10`. Bit 24
puts an ELF on kind `0x10`'s path, so it inherits kind `0x10`'s position.

That also joins two facts these notes already had and could not connect: the
toolkit sets the bit from cc 100 up, and both finalizability predicates
enumerate exactly cc 100 to 130 while rejecting cc <= 89. Cross-architecture
retargeting is a Blackwell-and-later facility, and the bit is its marker.

The name used for it here is `nominal_arch`. The parser's constant keeps its
original spelling, `FLAG_DEPRIORITISE`, with `FLAG_NOMINAL_ARCH` as an alias.

## Not established

Site 3 has never run on this project's hardware. It needs bit 24, an
architecture mismatch against the device, and a positive family answer, so it
needs a cc >= 100 GPU; on sm_89 the winner's architecture matches and the path
short-circuits at `0x47bbfc`. The 41 measured cases exercise sites 1 and 2 only,
and the `e_flags` rewrite is read out of the binary rather than observed.

Also open: what `CAN_FINALIZE_DEBUG` changes; where `state+0xb2` and policies 5
and `0xb` come from, both of which suppress bit 24 handling; whether any policy
puts `0x1000000` into the required-flags mask at `state+0x10`, which would make
the bit a hard admission gate; and whether the data center build behaves the
same, since `libcuda.so.610.57.04` is not on this machine.
