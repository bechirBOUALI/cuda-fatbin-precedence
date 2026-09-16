# What the scanner sees is not what the GPU runs

A CUDA fat binary is a container holding several compiled forms of the same
device code, one per GPU architecture the build targeted, plus optionally PTX
for architectures that did not exist yet. At load time the driver picks one and
runs it.

NVIDIA documents the coarse rule, and it is worth stating plainly because the
rest of this builds on it rather than overturning it. The Ampere compatibility
guide says that "if a cubin compatible with that GPU is present in the binary,
the cubin is used as-is for execution", otherwise the PTX is compiled at load
time. So a compatible cubin beats PTX. That much is settled.

What is not documented is what happens when that leaves more than one
candidate. The same guide says the runtime "uses this information to find the
best matching cubin or PTX version" and never says what "best matching" means.
There is no statement of how a nearer architecture ranks against a further one,
what breaks a tie between two entries that match equally well, or whether
position in the file matters. Those are the questions a tool has to answer to
know which entry runs.

Any tool that reads device code out of a binary, to disassemble it, hash it,
attest it, or decide whether it is safe, has to choose an entry. If its rule
and the driver's rule disagree, the tool is describing code the hardware never
runs, and it will not warn you, because from its point of view nothing is
wrong.

Watching what ran is a different question, and it is already answered. CUPTI
hands a profiler the payload the driver selected rather than the container, and
eBPF instrumentation spanning the CUDA API sees containers as they load and the
calls that follow them, which puts dynamic instrumentation in a better position
to attribute executed code than anything reading a file. What neither gives is
the answer before execution, on a file in a registry, or the mapping from an
observed kernel back to the shipped entry it came from. That is the question
here, and `analysis/prior-art.md` sets out what each existing mechanism does
and does not cover.

This is a measurement of that rule, a confirmation of it against the driver's
own code, and a parser that implements it.

## What was already known

Worth separating, so the new part is visible. Already documented by NVIDIA: a
compatible cubin is preferred over PTX; `CUDA_FORCE_PTX_JIT=1` ignores embedded
binary code and compiles the PTX instead; the `a` and `f` target suffixes,
`sm_90a` and `sm_100f`, and their compatibility semantics at the `nvcc` level;
and that the fat binary creation library enforces "only one entry per sm of
each unique identifier", which is why the conflicting containers below have to
be built deliberately.

Already reverse engineered publicly: the container layout, the wrapper, the
entry array and the entry header, most recently and most thoroughly in
Stealthium's write-up of the format, which names a `bin_info` bitfield carrying
platform, debug and compression bits but does not identify the selection bits
below. Several open-source parsers enumerate entries; none of them models which
entry the driver would pick.

New here, as far as I can establish, with the sources checked and listed in
`analysis/prior-art.md`: the ranking among candidates that all
match, which is the hierarchy below levels 0 and 1; the tie-break on flag bit
24; the file-order tie-break and the fact that its direction reverses between
cubin and PTX; the encoding of the `a` and `f` suffixes in two bits of the
entry flags, which is the container-level counterpart of a documented `nvcc`
feature; and the measurement of how far a conventional static reading lands
from what the GPU runs, on shipped libraries as well as on built cases.

## The rule

Selection is a hierarchy of five levels. Each one is consulted only when the
level above it ties.

| Level | Rule |
|---|---|
| 0. compatibility | entries the GPU cannot run leave the candidate set entirely |
| 1. kind | ELF beats kind 0x10 beats PTX, unconditionally |
| 2. architecture | among entries of one kind, the nearest compatible one wins, independent of file order |
| 3. flag bit 24 | among ELF entries still tied, the one **without** bit 24 wins |
| 4. file order | ELF: the first wins. PTX: the **last** wins |

Level 0 is a filter rather than a ranking, and it is load-bearing. Cubins are
compatible only inside their major generation, so an sm_75 cubin is not a
candidate on an sm_89 GPU, and a container holding only that entry is refused.
Put that same cubin beside a compute_75 PTX entry and the PTX runs in either
order, even though kind preference ranks ELF above PTX. The hierarchy only ever
operates on entries that could actually execute.

Every line of that was measured by loading containers built to conflict and
reading back which kernel ran. Two kernels export the same symbol and differ
only in a marker they write, so the marker names the entry the driver chose.

Levels 0 and 1 restate the documented rule and the measurements confirm it.
Everything below them is the part that was not written down, and three
consequences are worth stating separately, because each one breaks a different
reasonable-looking implementation.

**A PTX entry is unreachable whenever a compatible cubin is present**, which
follows from the documented rule but is sharper than it sounds. Kind is decided
before architecture, so no amount of architectural precision saves the PTX: an
sm_86 cubin beats an exactly matching compute_89 PTX on an sm_89 GPU. Nothing
in the PTX says it is dead, and `cuobjdump` lists it alongside the cubin
without comment. PTX is the tempting thing to analyse, because it is text
and the alternative needs a disassembler, and it is the thing least likely to
run.

**There is no single positional convention.** Two cubins at the same
architecture: the first runs. Two PTX entries at the same architecture: the
last runs. A tool that implements "first matching entry wins" is right for
cubins and wrong for PTX, and a tool that implements the opposite is wrong the
other way round.

**Position is only a tie-break.** The rule that looked positional at first is
the bottom level of three. Architecture proximity overrides it in both
directions: an sm_89 cubin beats an sm_86 cubin whichever comes first, and the
sm_86 cubin runs perfectly well on its own, so it is a real candidate rather
than an invalid entry being skipped.

## Why this is a rule and not an accident

Black-box measurement tells you what happened, not what will keep happening.
So the selection path was located in the driver binary: `libcuda.so.1.1` from
the WSL driver store, 25 MB, fully stripped, **driver 597.06**, the same build
every measurement above was taken on. Function boundaries come from `.eh_frame`
records, since there are no local symbols.

Three functions carry it: a container walk at `0x47ae40` holding an incumbent
and scanning entries in file order, a per-entry filter at `0x474aa0` deciding
whether an entry is a candidate at all, and a ranker at `0x474f20` handed the
incumbent and a challenger.

The walk advances by a stride it reads out of each entry, header size at
`+0x04` plus payload size at `+0x08`, so a parser has to walk the same way
rather than assume a fixed entry size.

Decompiled, the ranker states the precedence order outright. Kind 2 is ELF and
kind 1 is PTX:

```c
  if (kind_tmp != 2) {
    cand_kind = *candidate;
    if (cand_kind == 2) {
      return candidate;
    }
    if (kind_tmp == 0x10) {
      if (cand_kind != 0x10) {
        return incumbent;
      }
    }
    else {
      if (cand_kind == 0x10) {
        return candidate;
      }
      if (kind_tmp == 1) {
        if (cand_kind != 1) {
          return incumbent;
        }
      }
      else {
        if (cand_kind == 1) {
          return candidate;
        }
        // comparison of two kind-0x80 entries elided
      }
    }
    if (*(int *)(state + 8) == *(int *)(candidate + 0xe)) {
      return candidate;
    }
    return incumbent;
  }
```

Read the returns. An ELF candidate beats any non-ELF incumbent. Kind `0x10`
beats everything except ELF. PTX beats everything except those two. Because
each kind is tested on both sides before anything else is considered, no file
order can overturn it.

The same function's tail produced a finding the black-box work had missed. Two
cubins that tie on kind and architecture are separated by flag bit 24, and the
entry **without** it wins. That predicts something specific and falsifiable:
take two sm_89 cubins in the order A then B, where A normally wins because it
is first, and set bit 24 on A alone. Measured on the GPU, B runs instead. Set
it on B alone and A still runs. One bit reverses which of two same-architecture
kernels executes, while every size, offset, magic and architecture field stays
correct. The toolkit sets that bit on targets of compute capability 100 and
above, so this is ordinary metadata, not a reserved field.

The same method settles what flag bits 20 and 21 do, in the filter:

```c
      flags_bit20 = flags_lo & 0x100000;   // isolate flags bit 20
      if (*(uint *)(entry + 0xe) - 1 < 999) {   // arch bounded to 1..999
        suffix = "a";
        if ((*(ulong *)(entry + 0x14) & 0x100000) == 0) {
          suffix = "";
          if ((flags_lo & 0x200000) != 0) {
            suffix = "f";
          }
        }
        snprintf(name_buf,0xd,"%s_%d%s",&DAT_0136f4d8,arch_or_target_h,suffix);
```

The architecture is never compared as a number. It is formatted into a name and
the name is what selection matches on, with the suffix taken from those two
bits. An entry does not carry an architecture, it carries an architecture name,
and two flag bits change which name.

Two more causes, without the code. The filter dispatches on a policy field
through a sixteen-slot jump table at `0x150ea94`, whose slot 2 is literally
`cmp $0x1,%ax; jne reject`: under `CUDA_FORCE_PTX_JIT` cubin entries are
discarded before ranking rather than outranked, so the kind preference never
gets a say. And the JIT compiler loads from a function reached only after
selection, which is why a container with a PTX entry never loads the compiler
when a cubin wins.

The C above is Ghidra output with identifiers renamed and comments added,
nothing else altered. Ghidra scales pointer arithmetic by its own guessed
element type, so `entry + 0x14` is byte offset 0x28 and `entry + 0xe` is byte
offset 0x1c; the constants are reliable and the types are not. Its own labels,
`DAT_0136f4d8` above, carry a 0x100000 image base that the virtual addresses
quoted elsewhere here do not. Addresses are build-specific. The full decompilation is in
`analysis/decompiled-selection.md` and the disassembly walkthrough in
`analysis/driver-selection-logic.md`.

## What a static reading gets wrong

The three readings below are not measurements of any shipping product. They are
the plausible ways a tool could choose an entry, written so the driver's rule
has something to be compared against. Forty containers, each one loaded on the
GPU so that what executed is measured rather than predicted, and forty-one
cases, because one container is loaded under two host policies.

| How a tool picks the entry to inspect | Names an entry that did not run |
|---|---|
| first-match, the first entry the GPU could run | 17 of 41 |
| exact-arch, the first exact architecture match | 17 of 41 |
| prefer-PTX, read the text because it is text | 18 of 41 |
| precedence-aware, the rule above | 0 of 41 |

The full matrix, row by row with what each row establishes, is in
`analysis/divergence-matrix.md`.

The obvious objection is that these containers were built to conflict. So the
same readings were run against every fat binary in the CUDA toolkit's own
shipped libraries, which nobody here built or tampered with: 343 containers
across 14 libraries, of which 339 hold more than one entry.

The evidence here is one step weaker, and the distinction is worth keeping.
Those libraries carry no marker to read back, so nothing can be loaded and
observed; each reading is compared against the precedence-aware rule rather
than against hardware. That rule is not assumed correct, it is what the table
above establishes, agreeing with the GPU on all 40 built containers.

| How a tool picks the entry to inspect | Disagrees with the driver's rule |
|---|---|
| first-match | 342 of 343 |
| exact-arch | 195 of 343 |
| prefer-PTX | 342 of 343 |

No attacker is involved in that table. NVIDIA ships one entry per architecture
in ascending order, so a first-match reading reliably lands on the lowest,
which on this GPU is usually sm_75. A lone sm_75 cubin is refused outright on
an sm_89 GPU with `CUDA_ERROR_NO_BINARY_FOR_GPU`, because cubins are compatible
only inside their major generation. First-match is therefore not choosing a
suboptimal entry on real libraries. It is naming an entry the GPU would
refuse.

The careful reading, exact architecture match, is right on most shipped
libraries, and 192 of its 195 failures are in cuBLAS, which ships no sm_89
cubin at all: its containers carry sm_75, sm_80, sm_86, sm_90, sm_100 and
sm_120, so there is no exact match to find and it falls back to first-match.
Correct on benign input, wrong on the rest, is the least useful property a
scanner rule can have.

Nor does it take a multi-architecture build. An ordinary `nvcc -arch=sm_89 -c`
with no other flags emits one container holding an sm_89 cubin and a
compute_89 PTX entry, and `cuobjdump` lists both without distinction. The cubin
wins, so the PTX in the default output of a default compile is already dead
code. With no `-arch` at all the toolkit targets sm_75, and on this GPU that
inverts: the cubin is the wrong generation, so the PTX is the only candidate
and the driver compiles it at load time, while first-match and exact-arch both
report the cubin.

One more measurement matters for why this is worth caring about. Substituting
two bytes inside the compiled SASS of a valid entry, leaving every size, offset
and magic correct, produces a container that loads and runs the altered kernel.
Nothing validates entry contents, so an entry can be substituted as well as
mis-selected.

## The architecture is stated twice

A cubin entry declares its architecture in the fat binary entry header, and
again inside the embedded ELF in `e_flags`. Nothing makes the two agree, and
patching one while leaving the other shows which field does what. A header
claiming an unselectable architecture gives `CUDA_ERROR_NO_BINARY_FOR_GPU`: the
entry was never a candidate and its payload was never read. A header claiming a
selectable architecture over a payload for the wrong generation gives
`CUDA_ERROR_INVALID_SOURCE`: the entry was chosen, and only then was the ELF
looked at and refused. Selection reads the header, validation reads the ELF, in
that order.

Where both values are individually runnable, the mismatch passes in silence and
the container runs. Meanwhile `cuobjdump` reports the two fields through
different flags and contradicts itself on the same entry: `-lelf` prints the
ELF's value, `-elf` prints the header's. The listing is the machine-readable
output a tool is most likely to parse, and it is the one showing the field
selection does not use.

Selection also commits. Put an entry with a wrong-generation payload first and
a perfectly good sm_89 cubin second, and the load fails rather than falling back
to the good one. One entry is chosen and that decision is final, which is worth
knowing because "best matching" suggests otherwise.

## When the code cannot be read at all

The toolkit ships a keyed obfuscation feature, and the driver carries its two
labels, `PTX Obfuscation` and `TileIR Obfuscation`, in the container walk.
`fatbinary --okey=<n> -reorder-obfuscation` transforms a PTX payload and sets
bit 16 of the entry flags. The entry keeps its kind, and the container keeps
correct architecture, ISA version and size. The key itself is stored in the
entry header, in the eight bytes at offset 0x30 that are zero in ordinary
builds, encoded as the decimal digits of the key read as hex nibbles.

What changes is readability. `cuobjdump` reports the entry's metadata in full
and then says it cannot deobfuscate the entry without the key, followed by "No
PTX file found to extract", which is easy to read as an entry that simply has
no PTX. The driver returns `CUDA_ERROR_INVALID_PTX`, and its own logging says
why: the two strings in the walk are arguments to "Feature: '%s' not yet
implemented", so this driver recognises obfuscated payloads and does not
implement them.

The protection is thin. The key is in the container, and the transform is a
keyed byte-wise stream cipher over a substitution table that ships inside
`libnvfatbin`, so it is reversible from the file plus any toolkit installation.
Reimplementing it recovers the original PTX in full. `--okey` stops a tool that
has not been taught the format and stops nothing else.

This is an intellectual-property feature rather than a defect, and it is worth
naming because it is the limit case of everything above. Elsewhere the problem
is a tool reading the wrong entry. Here a tool can read no entry, while the
container still looks entirely well formed. A policy of "extract the PTX and
inspect it" needs "the PTX could not be extracted" as a distinct outcome, not
as an empty one. The parser reports it as such. Details are in
`analysis/ptx-obfuscation.md`.

## Two things that are not in the container at all

**The architecture is a name, and the listing drops half of it.** Two bits of
the entry `flags` field carry the architecture-name suffix, bit 20 for `a` and
bit 21 for `f`, and the driver renders the target as `sm_<arch><suffix>`. Those
are the suffixes `nvcc` exposes as `sm_90a` and `sm_100f`, confirmed by building
for each target and reading the bits back.

So setting bit 20 on an sm_89 cubin makes it declare `sm_89a`, a target no GPU
reports, and the container fails to load with
`CUDA_ERROR_NO_BINARY_FOR_GPU` while every magic, size and offset stays
correct. `cuobjdump` reports the suffix in one output and not the other:
`cuobjdump -lelf` lists the entry as `sm_89` and disassembles it fully, while
`cuobjdump -elf` shows `arch = sm_89a`. The field that decides whether the
entry runs is missing from the listing a tool parses.

**The live entry depends on the host, not only on the file.** With
`CUDA_FORCE_PTX_JIT=1` the PTX entry wins instead of the cubin. In the driver
this is not a demotion but a different filter: the environment variable sets a
policy field, the filter dispatches on it through a jump table, and under that
policy cubin entries are discarded before ranking happens. The practical effect
is that an analysis sandbox and a production host can disagree about what a fat
binary does without either being misconfigured, so the correct static answer is
a function of the file and the host together.

## The sizes are not lengths either

Selecting the right entry is only half the question. The other half is which
bytes are that entry, and the container's two size fields do not answer it.

`payload_size` advances the walk and does not bound the read. A PTX payload is
read to the first NUL however short the declared size is, and an ELF is read to
the extent its own headers describe, which can be longer or shorter than the
declared size. The consequence is measurable and blunt: two containers whose
declared payload bytes are byte-identical, `sha256 5f9458539df4b732` on both,
run different kernels on the GPU. A PTX entry declaring **zero** bytes of
payload runs a complete kernel. A tool hashing `payload[0 : payload_size]`,
which is the obvious implementation, therefore gives the same hash to different
code and a different hash to the same code. The collision is in the per-entry
hash specifically: the two containers differ elsewhere, so a hash over the
whole container still separates them, and the measurement is in
`analysis/declared-versus-executed.md`.

`fat_size` bounds the walk, but the driver truncates it to a signed 32-bit
value and an entry is walked when its **start** lies inside that bound. So an
entry whose header and payload lie entirely past the end of the declared
container executes, one byte of declared size decides whether it does, and
`cuobjdump` will not list it because it requires the whole 64-byte entry header
to fit. Between the two rules lies a 63-byte window in which the GPU runs an
entry no NVIDIA tool reports.

Neither of these needs a malformed file. Every container involved has correct
magics, correct architectures and untouched payload bytes; only the size fields
differ. The measurements, and what each reader sees for each case, are in
`analysis/declared-versus-executed.md`.

## The fix

`scripts/fatbin_entry_selection.py` reports, per entry, what it is and whether
the driver would execute it. `would_execute(entries, sm, policy)` implements the
hierarchy above, matches on the rendered architecture name so an `sm_89a` entry
is not a candidate for an sm_89 GPU, takes the target as an argument rather
than assuming the local GPU, and takes the host policy as an argument so the
`CUDA_FORCE_PTX_JIT` case is a parameter and not a second code path. It reads a
raw container, a shared library or executable through the registration
wrappers, and a relocatable object by walking `.nv_fatbin` directly, since in
an object file the wrapper's pointer is not filled in until link time. It
agrees with the driver on all forty-one measured cases, including the three
where the right answer is that nothing runs.

Three implementation details matter more than they look.

Payloads are hashed after decompression, not as stored. Hashing the stored
bytes would make identical device code hash differently depending only on the
compression setting, which is exactly the aliasing an attester must not have.

Decompression is driven by the flag bit, not the entry kind. A default build
compresses PTX and stores cubins raw, which makes keying off the kind look
right, but `fatbinary --compress-all` produces compressed cubin entries and
such a parser reads them as garbage.

The optional identifier and ptxas-options strings are bounds-checked against
the declared header size, and violations are recorded as notes rather than
raised, because a scanner has to keep going and report what it saw. All 343
shipped containers parsed with no notes.

For tooling that cannot adopt the rule, the minimum honest behaviour is to
report ambiguity rather than pick: if a container holds more than one candidate
for the target GPU, say so, and say which one would run.

## Scope, and what this is not

This is one GPU and one driver: RTX 2000 Ada, compute capability 8.9, driver
597.06, CUDA 13.2, Ubuntu 22.04 under WSL2. The rules were confirmed in the
driver binary as well as measured, so they are expected to hold more broadly,
but that is an expectation, and `would_execute` takes the architecture as an
argument so the corpus can be re-run elsewhere.

No selection behaviour here is a driver vulnerability. The driver applies its
own rule correctly and consistently. The gap is between that rule and the one a
convenient static reading uses, and it lives in the tooling, not in CUDA.

Where it matters is an attacker who can ship a binary, which is the real supply
chain for ML wheels and container images. It is not remote code execution.
Every payload here writes a marker value and nothing else.

Separately, containers whose declared sizes are malformed rather than merely
misleading can make the driver refuse, fault or fail to return. Those cases are
measured and are being reported to NVIDIA; they are deliberately not in this
repository, and nothing above depends on them.

Open items: kind 0x10 is an ELF the driver finalizes before load, on the
evidence of a handler whose whole error vocabulary is NVIDIA's Mercury
finalizer, but NVIDIA names it nowhere and that reading is an inference; and
several of the selector's policy values are visible in the jump table but
unidentified. The two size questions that were open here, a payload hidden in
the slack and an entry past the declared container size, are settled in
`analysis/declared-versus-executed.md`.

## Reproducing

```sh
make -C src/kernels                  # cubins, PTX, and the corpus containers
make -C src/harness                  # the loader
python3 scripts/divergence_matrix.py # the matrix, measured against the GPU
python3 scripts/survey_libs.py       # the same question on shipped libraries
python3 scripts/fatbin_entry_selection.py build/ptxa_elfb.fatbin
```

Set `CUDA_CACHE_DISABLE=1` for any manual run. Without it a cached JIT result
from an earlier run can be served back and recorded as a fresh selection
decision.

The evidence behind each finding is in `analysis/`: the measured rules in
`entry-precedence.md`, the full corpus in `divergence-matrix.md`, the driver
code as disassembly in `driver-selection-logic.md` and as decompiled C in
`decompiled-selection.md`, the size fields in
`declared-versus-executed.md`, the entry kinds in `fatbin-entry-kinds.md`, the obfuscation feature in `ptx-obfuscation.md`,
and what was already public in `prior-art.md`.
