# CUDA fat binary entry precedence

When a CUDA fat binary contains more than one entry matching the running GPU,
which one actually executes? NVIDIA documents the coarse rule, that a
compatible cubin is preferred over PTX, and then stops: the runtime is said to
find the "best matching" entry, with no statement of what breaks a tie between
entries that match equally well. That gap decides whether a tool inspecting GPU
code is looking at the code that runs.

This repository measures the rule, confirms it against the driver's own code,
and implements it.

**Start with [WRITEUP.md](WRITEUP.md).** It is the whole argument in one place,
in about 3200 words, with the decompiled C for the selection path.

## The rule, in short

Selection is a hierarchy, each level consulted only when the one above it ties.
The first two levels restate what NVIDIA documents; the ones below them are the
part that is not written down.

| Level | Rule |
|---|---|
| 1. kind | ELF beats PTX, unconditionally |
| 2. architecture | among entries of one kind, the nearest compatible one wins, independent of file order |
| 3. flag bit 24 | among ELF entries still tied, the one **without** bit 24 wins |
| 4. file order | ELF: the first wins. PTX: the **last** wins |

So a PTX entry is unreachable whenever a cubin for the same architecture is
present, and nothing in the PTX says so. `cuobjdump` lists both. An sm_86 cubin
beats an exactly matching compute_89 PTX on an sm_89 GPU, because kind decides
first.

Measured against the GPU on 29 containers built to conflict, and on 343
containers in NVIDIA's own shipped libraries:

| reading | wrong, built corpus | wrong, shipped libraries |
|---|---|---|
| first entry not exceeding the GPU | 15 of 29 | 342 of 343 |
| first entry matching the GPU exactly | 15 of 29 | 195 of 343 |
| the first PTX entry | 16 of 29 | 342 of 343 |
| the driver's rule, `would_execute()` | 0 of 29 | reference |

None of this needs a crafted file. An ordinary `nvcc -arch=sm_89 -c` already
emits a container whose PTX entry cannot run, and `cuobjdump` lists it without
comment.

Two further results. Bits 20 and 21 of an entry's `flags` field are the
architecture-name suffix, `a` and `f`, so an entry can declare `sm_89a` while
`cuobjdump -lelf` lists it as plain `sm_89` and disassembles it in full; no GPU
reports that target, so the container does not load. And with
`CUDA_FORCE_PTX_JIT=1` the PTX entry wins instead, so the same bytes run
different code on different hosts.

A cubin states its architecture twice, in the entry header and in the embedded
ELF, and the two can disagree: selection reads the header and validation reads
the ELF, while `cuobjdump` reports one through `-lelf` and the other through
`-elf`. Flag bit 24 decides between two otherwise equal cubins, and the entry without
it wins, so setting that one bit on the first of two entries makes the second
one execute.

Separately, a two-byte edit inside an entry's compiled SASS loads and runs, so
nothing validates entry contents. And the toolkit's keyed obfuscation feature,
flagged by bit 16, leaves an entry's metadata fully readable while making its
code opaque to `cuobjdump` and to the driver alike.

## Layout

```
WRITEUP.md   the argument, start here
analysis/    dated working notes, including measurements later corrected,
             the divergence matrix, the driver reverse engineering as both
             disassembly and decompiled C, and what was already documented
             or published before this work
scripts/     the parser, the divergence matrix, the shipped-library survey
src/         test kernels and a minimal Driver API loader
probes/      small programs that identify entry kinds via libnvfatbin
```

`analysis/` is a lab notebook kept in the order it was written, so earlier
files contain statements that later files correct. `WRITEUP.md` is the result.

## Reproducing

Needs a CUDA toolkit, a supported GPU, and Python with `pyelftools` and
`zstandard`.

```sh
make -C src/kernels                  # cubins, PTX, and 20 conflict containers
make -C src/harness                  # the loader
python3 scripts/divergence_matrix.py # the matrix, measured against the GPU
python3 scripts/survey_libs.py       # the same question on shipped libraries
python3 scripts/fatbin_parser.py build/ptxa_elfb.fatbin
```

Set `CUDA_CACHE_DISABLE=1` for any manual run, or a cached JIT result can be
mistaken for a fresh selection decision. The matrix script sets it itself.

## Environment these results came from

NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9, driver 597.06,
CUDA 13.2, Ubuntu 22.04 under WSL2. Measurements are conditional on that. The
precedence rules were also read out of the driver binary, so they are expected
to hold more broadly, but that is an expectation and not a measurement.

Nothing here is a driver vulnerability. The driver applies its own rule
correctly and consistently; the gap is between that rule and the one a
convenient static reading uses. Every payload in this repository writes a
marker value and nothing else.
