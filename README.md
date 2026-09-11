# CUDA fat binary entry precedence

When a CUDA fat binary contains more than one entry matching the running GPU,
which one actually executes? The format is only partly documented, the
precedence rule is not documented at all, and the answer matters to anyone
inspecting GPU code statically.

This repository measures the rule, confirms it against the driver's own code,
and provides the tools used.

## Findings

Selection is a **three-level hierarchy**, not a single rule. Each level is only
consulted when the one above it ties.

**1. Entry kind decides first.** An ELF beats a PTX entry even when the PTX is an
exact architecture match and the ELF is not. An sm_86 cubin beats a compute_89
PTX on an sm_89 GPU, in either file order.

**2. Then architecture proximity.** Among entries of the same kind, the nearest
compatible architecture wins **regardless of file order**. An sm_89 cubin beats
an sm_86 cubin whichever comes first, and the sm_86 cubin runs perfectly well on
its own, so it is a genuine candidate rather than an invalid one.

**3. Only then file order**, and its direction depends on the kind:

| Same kind, same architecture | Winner |
|---|---|
| ELF + ELF | **first** in file order |
| PTX + PTX | **last** in file order |

The practical consequence is sharper than "there is an undocumented rule".
A tool that implements "first matching entry wins" is wrong three ways: whenever
entries differ in kind, whenever they differ in architecture, and whenever they
are PTX. File order is a tie-break at the bottom of the hierarchy, not the rule.

**Between a PTX entry and an ELF entry for the same architecture, the ELF wins
regardless of position.** Entry kind outranks order.

**A PTX entry is therefore unreachable whenever a cubin for the same
architecture is present, and nothing in the PTX says so.** `cuobjdump` lists
both. Any analysis that reads the PTX, which is the tempting choice since PTX
is text while the alternative needs disassembly, is describing code that never
executes. Distinguishing live from dead entries requires applying the driver's
precedence rule, which is undocumented.

**The live entry is also environment-dependent.** With `CUDA_FORCE_PTX_JIT=1`
the PTX entry wins instead, so an analysis sandbox and a production host can
disagree about what a fat binary does without either being misconfigured.

**Entry kinds identified by construction:** 1 = PTX, 2 = ELF/cubin, 8 = LTO IR,
0x40 = relocatable PTX. Kinds 0x10, 0x20 and 0x80 remain unidentified; 0x100 is
a group/index entry that NVIDIA's own header says cannot currently be created.

**A single flag bit removes an entry from selection while leaving it fully
visible to tooling.** Setting bit 20 or bit 21 of an entry's `flags` field makes
the driver refuse a container holding one otherwise valid cubin, returning
"no binary for GPU", while `cuobjdump` lists that same entry and disassembles it
completely. Neither side warns. The container is self-consistent: every size and
offset is correct, and only one bit differs from a working file.

| flags bit set | Driver | cuobjdump |
|---|---|---|
| none | runs it | disassembles fully |
| bit 20 | refuses | disassembles fully |
| bit 21 | refuses | disassembles fully |
| bit 24 | runs it | disassembles fully |

**The container carries no per-entry integrity metadata**, and the driver
computes none at load time. Two entries differing only in payload produce
containers differing only in those payload bytes.

## Layout

```
analysis/   measured results and the reverse engineering behind them
src/        test kernels and a minimal Driver API loader
scripts/    fat binary parser
probes/     small programs that identify entry kinds via libnvfatbin
```

## Reproducing

Needs a CUDA toolkit and any supported GPU.

```sh
make -C src/kernels     # cubins, PTX, and fat binaries with conflicting entries
make -C src/harness     # the loader
cd build
export CUDA_CACHE_DISABLE=1
./loader elf_ab.fatbin      # first ELF wins
./loader elf_ba.fatbin      # order swapped, winner swaps
./loader ptxa_elfb.fatbin   # ELF wins over PTX
CUDA_FORCE_PTX_JIT=1 ./loader ptxa_elfb.fatbin   # now PTX wins
```

Set `CUDA_CACHE_DISABLE=1` for every run, or a cached JIT result can be
mistaken for a fresh selection decision.

## Environment these results came from

NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9, driver 597.06,
CUDA 13.2, Ubuntu 22.04 under WSL2. Numbers are conditional on that; the
precedence rules were confirmed in the driver binary and are expected to hold
more broadly.
