# CUDA fat binary entry precedence

When a CUDA fat binary contains more than one entry matching the running GPU,
which one actually executes? The format is only partly documented, the
precedence rule is not documented at all, and the answer matters to anyone
inspecting GPU code statically.

This repository measures the rule, confirms it against the driver's own code,
and provides the tools used.

## Findings

**Between two ELF entries for the same architecture, the first in file order
wins.** Swapping the order swaps the winner, so selection is positional.

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
