# Where fatbin entry selection actually happens

Reverse engineering of the CUDA user-mode driver, 2026-09-10, to explain the
black-box precedence rules recorded in `step1-entry-precedence.md`.

Target: `libcuda.so.1.1`, 25 MB, fully stripped, from the WSL driver store
(`/usr/lib/wsl/drivers/nvlt.inf_amd64_3164510f0d5369d2/`). Driver 595.71.
Note this is NOT `/usr/lib/wsl/lib/libcuda.so.1`, which is a 180 KB forwarding
shim.

Two independent analyses were run, one with Ghidra and one with objdump only.
They agreed on every address below. Addresses are file virtual addresses; the
saved Ghidra project uses an image base of 0x100000, so Ghidra address = VA +
0x100000.

## The three functions

| Address | Role |
|---|---|
| `0x47a540` | container walk and selection loop; fatbin case at `0x47ad80` |
| `0x474050` | per-entry filter: is this entry a candidate at all |
| `0x474540` | ranker: given incumbent and candidate, which wins |

The loop is a linear scan in file order holding an incumbent. The first
matching entry becomes the incumbent; each later match is passed to the ranker.

## How each observed behaviour arises

**Two ELF entries, same arch: first wins.** The ranker ends with a comparison
that returns the incumbent on a tie (`cmovae` at `0x47495b`). Since the
incumbent is the earlier entry, ties go to file order.

**ELF beats PTX regardless of order.** Two symmetric hard-coded rules, at
`0x474766` and `0x474906`, test kind 2 on each side before any other
comparison. Rank order is ELF (2) > 0x10 > PTX (1) > everything else. Order
cannot influence a hard-coded kind preference, which is why swapping had no
effect.

**`CUDA_FORCE_PTX_JIT` inverts it.** String at `0x126da26`, single code
cross-reference at `0x28438b`. It sets bit 0x10 of a global flags byte at
`0x1821114`, read at `0x28f4e0`, which sets the selector's policy field to 2.
The filter dispatches policy through a jump table at `0x150da94`; slot 2 lands
at `0x4742f0`, which is literally `cmp $0x1,%ax; jne reject`. Under that policy
ELF entries are **discarded before ranking**, not outranked by PTX.

**The JIT loads only when PTX wins.** The winning entry's kind is stored in the
handle at `0x47b162`. The JIT dispatcher reads it afterwards and only then
calls `dlopen("libnvidia-ptxjitcompiler.so.1")` at `0x4714c0`. Selection
therefore completes before compilation is even considered, which matches the
observation that a fatbin containing a PTX entry never loads the compiler when
an ELF entry wins.

## Entry header layout

Recovered by RE, then validated by parsing our own fatbins.

| Offset | Type | Field |
|---|---|---|
| 0x00 | u16 | kind, **1 = PTX, 2 = ELF** |
| 0x04 | u32 | headerSize |
| 0x08 | u64 | payloadSize |
| 0x10 | u32 | compressedSize |
| 0x18 | u32 | ISA version |
| 0x1c | u32 | arch (89 for sm_89) |
| 0x28 | u64 | flags |
| 0x30 | u64 | uncompressed size |

Payload starts at `entry + headerSize`. Next entry is at
`entry + headerSize + payloadSize`. Minimum header size is 0x40.

Validation against `build/elf_ab.fatbin`: two entries, both kind 2, both
arch 89, header 64, payload 3112, stride 3176. That stride independently
matches the 3176-byte gap measured between marker constants before any RE was
done, and the 64-byte header matches the 64 bytes of per-entry container
overhead inferred from the same measurement.

Validation against `build/ptxa_elfb.fatbin`: entry 1 kind 1 (PTX), header 80,
payload 232, flags 0x8011, payload beginning `28 b5 2f fd`, the Zstandard
magic. Entry 2 kind 2 (ELF), header 64, payload 3112, flags 0x11, payload
beginning `\x7fELF`. So **PTX payloads are zstd-compressed while ELF payloads
are stored raw**, and flag bit 0x8000 correlates with compression.

## Integrity: there is none

The Ghidra pass disassembled the whole fatbinary code region and searched for
CRC constants, FNV constants, and MD5/SHA routines. No integrity check of any
kind is applied to entries. The only hash on the path is an FNV-1a-64 at
`0x2a46f3`, computed *after* selection purely as a JIT-cache key.

This closes the argument from the container side. We already knew from byte
diffing that the format carries no per-entry checksum. The driver does not
compute one at load either. Payload substitution is therefore undetectable at
both layers.

## Confidence and open items

High confidence, verified at instruction level by both analyses and validated
against real files: the three function addresses, the kind constants, the kind
preference order, the `CUDA_FORCE_PTX_JIT` chain, the entry layout, and the
absence of integrity checking.

Inferred rather than proven: the default policy value, the meaning of flag bit
24 in ELF-versus-ELF ranking, the semantics of kinds 8, 0x10, 0x20 and 0x80,
and the kind-0x100 nested group format, which was read from code with no sample
to check against.

Method: two independent reverse-engineering passes, one with Ghidra and one
with objdump only, which agreed on every address. Findings were then validated
by parsing real fat binaries.
