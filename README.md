# CUDA fat binary entry precedence

A CUDA fat binary holds several compiled forms of the same GPU code. The driver
picks exactly one and runs it. Any tool that inspects, hashes or attests that
code has to make the same choice, and where its rule and the driver's rule
disagree it is describing code the hardware never executes. Neither side warns
you.

NVIDIA documents the coarse rule, that a compatible cubin is preferred over
PTX, and then stops: the runtime is said to find the "best matching" entry,
with no statement of what happens when several entries match equally well.
Every finding below lies beneath that line.

## What the driver actually does

| # | Finding | Evidence |
|---|---|---|
| 1 | Selection is a hierarchy: compatibility filter, then kind, then architecture proximity, then flag bit 24, then file order | [entry-precedence](analysis/entry-precedence.md) |
| 2 | File order reverses by kind: the **first** cubin wins, the **last** PTX wins | [entry-precedence](analysis/entry-precedence.md) |
| 3 | Flag bit 24 breaks a cubin tie, and the entry **without** it wins | [entry-precedence](analysis/entry-precedence.md) |
| 4 | Flag bits 20 and 21 encode the `a` and `f` architecture-name suffix; the driver matches on the rendered name `sm_<arch><suffix>` | [driver-selection-logic](analysis/driver-selection-logic.md) |
| 5 | Architecture is declared twice; selection reads the entry header, validation reads the embedded ELF, in that order | [entry-precedence](analysis/entry-precedence.md) |
| 6 | Selection commits: a refused payload does not fall back to a good entry sitting next to it | [entry-precedence](analysis/entry-precedence.md) |
| 7 | Nothing validates payloads; a two-byte edit inside compiled SASS loads and runs | [driver-selection-logic](analysis/driver-selection-logic.md) |
| 8 | Flag bit 16 marks obfuscation; the key sits in the entry header and the transform is reversible from the file alone | [ptx-obfuscation](analysis/ptx-obfuscation.md) |
| 9 | `--okey` collides two inputs through a decimal-to-hex round trip, leaving well under 32 bits of key space | [ptx-obfuscation](analysis/ptx-obfuscation.md) |
| 10 | Decompression is keyed by a flag bit rather than by entry kind, and the decompressed size is not where format notes place it | [driver-selection-logic](analysis/driver-selection-logic.md) |
| 11 | Entry kinds 0x20, 0x80 and 0x100 are `index`, `tile ir` and `contatenated entry`, NVIDIA's own spelling | [fatbin-entry-kinds](analysis/fatbin-entry-kinds.md) |
| 12 | Kind 0x10 is an ELF the driver finalizes before load. **Inference**, not confirmed: NVIDIA names it nowhere | [fatbin-entry-kinds](analysis/fatbin-entry-kinds.md) |

![One fat binary, six entries, five eliminated by header fields, one running on the GPU](docs/entry-selection.gif)

Six entries compiled from one kernel, and five are dead before the GPU sees
anything. Each gate crosses out the entry it rejects and marks the field that
did it: the wrong cubin generation, the kind that outranks it, the further
architecture, the flag bit, the file position. Entry 4 survives and runs.

That container is real. Building it and clearing bit 24 on entry 3 changes the
marker the GPU returns from `0xBBBB` to `0xAAAA`, because entry 3 then wins on
file order instead. Nothing else in the file changes.

Open [docs/entry-selection.html](docs/entry-selection.html) for the same
walkthrough with a pause control, or
[docs/entry-explorer.html](docs/entry-explorer.html) to edit the entries and
flag bits yourself and watch the rule decide.

The selection path is given as disassembly in
[driver-selection-logic](analysis/driver-selection-logic.md) and as decompiled
C in [decompiled-selection](analysis/decompiled-selection.md). What was already
public before this work is set out in [prior-art](analysis/prior-art.md).
[WRITEUP.md](WRITEUP.md) is the whole argument read end to end.

## This is not only a laptop result

The measurements were taken on a laptop GPU, but the same selection code ships
in NVIDIA's Linux **data center** driver, the branch validated for HGX
A100/A800, H100 and H800. Confirmed by static comparison against
`libcuda.so.610.57.04`: the architecture-name rendering including both suffix
bits, the flag bit 24 tie-break, the kind cascade, the TileIR dispatch through
`libnvidia-tileiras.so` and the obfuscation path all appear there in the same
shape.

Two limits, stated plainly. Nothing was executed on that driver, since no data
center hardware was available, so this is static evidence that the code is
present rather than a measurement that it behaves identically. And that build
carries additional selector policies absent from the laptop driver, including
one that reorders the kind hierarchy, so behaviour under those policies is
uncharacterised.

## Why it matters

Selection only matters when a container holds more than one candidate, and it
almost always does: **339 of the 343** fat binaries in the CUDA toolkit's own
shipped libraries carry more than one entry. Any tool reading GPU code out of a
binary therefore has to decide which entry it is reading, on nearly every file
it meets.

Nothing in the container answers that for it, and the shipped tooling does not
either. `cuobjdump` lists every entry with nothing marking which one the driver
would run. It reports a different architecture for the same entry through
`-lelf` than through `-elf`. And for an obfuscated entry it prints "No PTX file
found to extract", which reads like an entry with no PTX rather than one whose
PTX it could not decode. Those are measurements of NVIDIA's own tools.

It takes no crafted file to reach this. An ordinary `nvcc -arch=sm_89 -c`
already emits a container whose PTX entry can never run on the GPU it was built
for, and nothing in the output says so.

Knowing the rule is what closes the gap. A tool that reproduces the driver's
precedence can point at the entry that will actually execute, hash that one,
disassemble that one, and say plainly when a container has no runnable entry at
all. That is what `would_execute()` does, and
[divergence-matrix](analysis/divergence-matrix.md) is how it was checked: 29
containers built to put the rules in conflict, each loaded on a real GPU, with
the rule agreeing with hardware on every one.

## What the parser adds

`scripts/fatbin_parser.py` answers the question the format does not: for each
entry, whether the driver would execute it.

- **It can answer "nothing runs."** A first-match scanner structurally cannot
  produce that verdict, yet it is the correct answer for three container shapes
  here.
- **It takes the target and the host policy as arguments**, so `sm_90a` and a
  `CUDA_FORCE_PTX_JIT` host are parameters rather than separate code paths.
- **It hashes the decompressed payload**, so identical device code cannot hash
  differently merely because a compression setting changed.
- **It decompresses on the flag, not the kind**, which is what stops compressed
  cubins from being read as garbage.
- **It flags an architecture disagreement** between the entry header and the
  embedded ELF, a field `cuobjdump` reports inconsistently across its own two
  modes.
- **It reports an obfuscated entry as a distinct outcome**, not as an entry
  with no code, which is how the shipped tooling presents it.

All 343 shipped containers parse with no structural complaint, so it is
exercised on real code and not only on its own corpus.

## Reproducing

Needs a CUDA toolkit, a supported GPU, and Python with `pyelftools` and
`zstandard`.

```sh
make -C src/kernels                  # cubins, PTX, and 29 conflict containers
make -C src/harness                  # the loader
python3 scripts/divergence_matrix.py # the matrix, measured against the GPU
python3 scripts/survey_libs.py       # the same question on shipped libraries
python3 scripts/fatbin_parser.py build/ptxa_elfb.fatbin
```

Set `CUDA_CACHE_DISABLE=1` for any manual run, or a cached JIT result can be
mistaken for a fresh selection decision. The matrix script sets it itself.

## Layout

```
WRITEUP.md   the argument end to end
docs/        the selection walkthrough as a GIF and as two live pages
analysis/    the evidence behind each finding above
scripts/     the parser, the divergence matrix, the shipped-library survey
src/         test kernels and a minimal Driver API loader
probes/      programs that identify entry kinds via libnvfatbin
```

## Scope

NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9, driver 597.06,
CUDA 13.2, Ubuntu 22.04 under WSL2. Every measurement is conditional on that.
The driver reverse engineering was done on the same build, so both halves agree
on version, and the addresses are build-specific: they will not survive a
driver update.

Nothing here is a driver vulnerability. The driver applies its own rule
correctly and consistently; the gap is between that rule and the one a
convenient static reading uses, and it lives in the tooling. Every payload in
this repository writes a marker value and nothing else.
