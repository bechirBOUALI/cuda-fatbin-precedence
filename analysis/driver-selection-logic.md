# Where fatbin entry selection actually happens

Reverse engineering of the CUDA user-mode driver to explain the precedence
rules measured in `step1-entry-precedence.md` and `divergence-matrix.md`.

Target: `libcuda.so.1.1`, 25 MB, fully stripped, from the WSL driver store at
`/usr/lib/wsl/drivers/nvltwi.inf_amd64_508a7ec7f027b810/`. **Driver 597.06**,
which is the same driver every measurement in this repository was taken on.
Note this is NOT `/usr/lib/wsl/lib/libcuda.so.1`, which is a 180 KB forwarding
shim.

Redone 2026-09-12. An earlier pass covered driver 595.71; those addresses do
not survive the update and are gone from this document, because carrying
addresses from a build you no longer have is worse than having none. Function
boundaries here come from `.eh_frame` FDE records, since the binary has no
local symbols, and virtual address equals file offset in this image because
the first `LOAD` segment maps offset 0 at vaddr 0.

## The path, function by function

| Range | Role |
|---|---|
| `0x4772d0..0x47790e` | image classifier: what kind of thing was handed to `cuModuleLoadData` |
| `0x47ae40..0x47c706` | container walk and selection loop |
| `0x474aa0..0x474f20` | per-entry filter: is this entry a candidate at all |
| `0x474f20..0x4753fb` | ranker: given incumbent and candidate, which one wins |
| `0x472100..0x4721d1` | loads the PTX JIT compiler |

### Image classification

At `0x4773ad` the classifier loads `0x1ba55ed50`, masks the first 48 bits of
the image with `0xffffffffffff` and compares, so the container magic
`0xba55ed50` and the version word are checked as a single value. The fat binary
branch is taken at `0x4773ca` to `0x477658`, which only records an image-type
tag of 2 at `0x47765f`. Two sibling branches handle a bare cubin, `cmp
$0x464c457f` at `0x4773d2`, and another container magic `0x1ee55a01` at
`0x4773dd`; failing all of those it sniffs for PTX text by skipping whitespace
and looking for a `//` comment.

### The walk

The loop reads the container header exactly as a parser would:

```
47b687:  mov    0x58(%rdx),%rax      ; container base
47b68b:  movzwl 0x6(%rax),%ebx       ; header_size, u16 at +0x06
47b68f:  mov    0x8(%rax),%rsi       ; fat_size,    u64 at +0x08
47b69d:  add    %rax,%r14            ; first entry = base + header_size
```

and then walks entries by stride, holding an incumbent in `%r15`:

```
47b6c8:  mov    %r14,%rsi            ; candidate entry
47b6cb:  mov    %rbx,%rdi            ; selector state
47b6ce:  call   474aa0               ; the filter, result in %al
47b6d3:  test   %al,%al
47b6d5:  jne    47b810               ; accepted, go and rank it
47b6e6:  mov    0x4(%r14),%eax       ; entry header_size, +0x04
47b6ea:  add    0x8(%r14),%rax       ; + payload_size,    +0x08
47b6ee:  add    %rax,%r14            ; next entry
47b6fe:  cmp    %rax,%rdx            ; bounded by fat_size
47b701:  jge    47b9fa
47b707:  cmpw   $0x100,(%r14)        ; kind 0x100, the group entry
```

Two things worth recording. The stride is `header_size + payload_size` taken
from the entry itself, which is why a precedence-aware parser has to walk the
same way rather than assume a fixed entry size. And a kind `0x100` entry is
handled by a nested walk over a table whose offset is read from `entry+0x14`
at `0x47b70f`, so that field is not a fixed-purpose field: for PTX entries it
locates the ptxas-options descriptor, and for a group entry it locates the
group table.

### The filter

`0x474aa0` reads the entry kind as a u16 at offset 0 and dispatches on it:

```
474ab4:  movzwl (%rsi),%eax
474ab7:  cmp    $0x20,%ax
474ac2:  cmp    $0x8,%ax
474aca:  cmp    $0x10,%ax
474af0:  cmp    $0x80,%ax
474b10:  lea    -0x1(%rax),%edx ; cmp $0x1,%dx   ; kinds 1 and 2 together
```

Then it dispatches on a policy field held at `+0x0c` of the selector state,
bounds-checked to 15, through a jump table in `.rodata`:

```
474ad0:  cmpl   $0xf,0xc(%r12)
474add:  lea    0x1099fb0(%rip),%rcx   # 150ea94
474ae4:  movslq (%rcx,%rdx,4),%rdx
474aeb:  jmp    *%rdx
```

All sixteen slots of that table resolve inside the filter. Two are worth
naming:

| Slot | Target | Code |
|---|---|---|
| 2 | `0x474d40` | `cmp $0x1,%ax; jne reject`, accept only PTX |
| 7 | `0x474d50` | `cmp $0x10,%ax; jne reject`, accept only kind 0x10 |
| default | `0x474b40` | the ordinary path below |

Slot 2 is the `CUDA_FORCE_PTX_JIT` policy, and it explains the measured
behaviour exactly: under it, ELF entries are **discarded before ranking**
rather than outranked by PTX, so the kind preference never gets a say.

The default path at `0x474b40` reads the two entry fields selection turns on:

```
474b40:  mov    0x28(%rbx),%rax      ; flags, +0x28
474b44:  mov    0x1c(%rbx),%r8d      ; arch,  +0x1c
474b4b:  lea    -0x1(%r8),%edx
474b4f:  and    $0x100000,%ecx       ; flags bit 20
474b59:  cmp    $0x3e6,%edx          ; arch must be 1..999
474b5f:  ja     474cec
```

with bit 21 tested at `0x474cbe` and `0x474d00`.

### The ranker

`0x474f20` is called from three sites in the walk, `0x47b7dd`, `0x47b8ed` and
`0x47b990`, with the selector state, the incumbent and the candidate, and its
return value becomes the new incumbent. The top-level one is `0x47b990`; this
excerpt is the shape they share:

```
47b8e4:  mov    %r15,%rsi            ; incumbent
47b8e7:  mov    %r13,%rdx            ; candidate
47b8ea:  mov    %rbx,%rdi
47b8ed:  call   474f20
47b8f2:  mov    %rax,%r15
```

Inside, kinds are tested symmetrically for the two sides, which is why no file
order can overturn a kind preference:

```
474fcc:  cmpw   $0x1,(%rbx)          ; incumbent is PTX
474fde:  cmp    $0x1,%ax             ; candidate is PTX
474feb:  cmp    $0x80,%dx
474ff6:  cmp    $0x80,%ax
475000:  cmp    $0x8,%dx             ; kind 8, LTO IR
47500a:  cmp    $0x8,%ax
475029:  cmp    %eax,0x0(%r13)       ; architectures compared
```

Its tail separates two entries that tie on kind and architecture, and flag bit
24 decides there: where exactly one of the two carries `0x1000000`, the entry
**without** it is returned. Only when both agree on that bit does the last
comparison run, on a u16 taken from an identifier structure built per entry,
with the candidate needing to be strictly greater to displace the incumbent.
Ties therefore go to the incumbent, which is the earlier entry, and that is the
measured "first ELF wins".

This was read out of the decompilation and then measured, which is the right
order for a claim like it. Two sm_89 cubins ordered A then B give A. Setting
bit 24 on A alone gives B instead; setting it on B alone changes nothing. Both
containers are in the corpus, and the C is in `decompiled-selection.md`.

## What flag bits 20 and 21 actually are

This corrects the earlier reading of them as an exclusion mechanism.

The ranker renders each entry's target as a **name**, and the suffix of that
name comes from those two bits:

```
474f4d:  mov    0x28(%rsi),%rax                ; entry flags
474f58:  test   $0x100000,%eax                 ; bit 20
474f5d:  jne    474f76                         ;   suffix = "a"
474f5f:  test   $0x200000,%eax                 ; bit 21
474f64:  lea    0x10a06ce(%rip),%r9   # 1515639  ; "f"
474f6b:  lea    0x10a0165(%rip),%rax  # 15150d7  ; ""
474f72:  cmove  %rax,%r9
474f7a:  lea    0xdfa557(%rip),%rcx   # 126f4d8  ; "sm"
474f86:  lea    0xdfa54e(%rip),%rdx   # 126f4db  ; "%s_%d%s"
474f92:  call   snprintf
```

So the target is `sm_<arch><suffix>`, with the suffix `a` for bit 20, `f` for
bit 21, and empty otherwise. Those are the suffixes `nvcc` exposes as `sm_90a`
and `sm_100f`. Confirmed by construction, building one container per target and
reading the bits back:

| `nvcc -arch=` | arch field | flags | bit 20 | bit 21 |
|---|---|---|---|---|
| `sm_90` | 90 | `0x11` | no | no |
| `sm_90a` | 90 | `0x100011` | **yes** | no |
| `sm_100` | 100 | `0x1000011` | no | no |
| `sm_100f` | 100 | `0x1200011` | no | **yes** |
| `sm_120a` | 120 | `0x1100011` | **yes** | no |

This changes what the measured behaviour means. Setting bit 20 on an sm_89
entry does not hide it behind a flag; it makes the entry declare `sm_89a`, a
target no GPU reports, so the container has no candidate and the load fails
with `CUDA_ERROR_NO_BINARY_FOR_GPU`. Bit 24, which had looked like an inert
control, is simply not part of the suffix encoding: it appears on compute
capability 100 and above regardless of suffix.

The divergence survives the correction, and gets sharper. `cuobjdump` reads the
bit and reports it in one place but not the other:

```
$ cuobjdump -lelf build/flag20_elfa.fatbin
ELF file    1: flag20_elfa.1.sm_89.cubin        <- no suffix
$ cuobjdump -elf build/flag20_elfa.fatbin | grep arch
arch = sm_89a                                    <- suffix
```

The entry listing, which is the output a tool parses, drops the suffix that
decides whether the entry can run at all.

## Integrity: measured, not inferred

The earlier pass argued from disassembly that no integrity check exists. That
is answered better by execution, so it was tested instead. Substituting two
bytes inside the compiled SASS of a valid entry, leaving every size, offset and
magic correct:

```
$ make -C src/kernels tamper
tampered_payload.fatbin: payload marker 0xAAAA -> 0xCCCC at offset 0x714
$ CUDA_CACHE_DISABLE=1 ./build/loader build/tampered_payload.fatbin
marker  : 0xCCCC -> unrecognised
```

The tampered kernel loads and executes, so neither the container nor the driver
validates entry payloads. This matches the container-side evidence in
`step1-entry-precedence.md`, where two containers differing only in their
payloads are otherwise byte-identical, and it needs no assumptions about code
the disassembly might have hidden.

For the record on the driver side: FNV-1a-64 constants do occur in this
binary, at `0x1fa354` and nearby, but in a region unrelated to the selection
path. No hash or checksum over entry payloads was identified among the
functions the walk calls. That is a weaker statement than the measurement
above and is offered only as corroboration.

## The CUDA_FORCE_PTX_JIT chain, end to end

| Address | What happens |
|---|---|
| `0x126ea46` | the string `CUDA_FORCE_PTX_JIT`, with a single code cross-reference |
| `0x2847cb` | that cross-reference, inside the environment-variable parser |
| `0x28486f` | the parsed result is shifted to bit 4, i.e. `0x10` |
| `0x28488e` | written into a global flags byte at `0x182211c` |
| `0x28f950` | `testb $0x10` on that byte, and if set `movl $0x2` into the selector state |
| `0x150ea94` slot 2 | `0x474d40`, which accepts only kind 1 |

The JIT loads only when PTX wins, because the compiler is loaded from
`0x472100..0x4721d1`, reached after selection, using the string
`libnvidia-ptxjitcompiler.so.1` at `0x150ea15`.

## Confidence

Verified at instruction level on this binary: every address and code excerpt
above, the jump table contents, the container and entry field offsets, the
suffix encoding, and the `CUDA_FORCE_PTX_JIT` chain. The suffix encoding is
additionally confirmed by construction, and the absence of payload validation
by execution.

Not established: the meaning of policy values other than 2 and 7, though the
incumbent path at `0x47b819` reads the same field and branches on 1, `0xa` and
`0xc`, and the ranker adds 3 and 6, so there are at least seven live policies.
Kinds `0x20` and `0x80` are dispatched by the filter but their semantics are
unknown. Why bit 24 loses the tie-break rather than winning it, and what the
identifier field the final comparison uses actually contains. And whether a
genuine `sm_90a` cubin is a candidate on a cc 9.0 device, which needs hardware
not available here.

Method: function boundaries from `.eh_frame`, disassembly with objdump, then
every structural claim re-checked by building containers with known properties
and reading the fields back. The same three functions are given as decompiled
C in `decompiled-selection.md`, produced independently with Ghidra; it agreed
with this document on every address, offset and constant.
