# Prior art, checked 2026-09-14

The repository claims that the rule deciding which fat binary entry executes is
not documented. That claim is load-bearing, so it was checked against the
sources rather than assumed, and it came back partly wrong. This note records
what was found and what changed as a result.

## Documented by NVIDIA

**A compatible cubin is preferred over PTX.** The Ampere compatibility guide is
explicit: "If a cubin compatible with that GPU is present in the binary, the
cubin is used as-is for execution", otherwise the runtime JIT-compiles the PTX.
The same statement appears in the Pascal and Blackwell guides and, less
formally, in the CUDA Pro Tip post on fat binaries and in NVIDIA staff answers
on the developer forums.

This means levels 0 and 1 of the hierarchy in `entry-precedence.md`
restate documented behaviour. The measurements confirm it. They do not discover
it, and the write-up now says so.

**Selection is described only as "best matching".** The same guide says the
runtime "uses this information to find the best matching cubin or PTX version"
and never defines the phrase. Nothing states how a nearer architecture ranks
against a further one, what happens when two entries match equally well, or
whether file position matters. That is the gap this work fills.

**`CUDA_FORCE_PTX_JIT=1`** is documented as causing embedded binary code to be
ignored in favour of JIT-compiling PTX. The measured behaviour matches. What
the driver code adds is the mechanism, that cubin entries are discarded by a
policy-specific filter before ranking rather than demoted during it.

**The `a` and `f` target suffixes are documented at the `nvcc` level.**
`sm_90a` is architecture-specific and not forward compatible; `sm_100f` is
family-specific and compatible across a minor-version family. Both are covered
in the nvcc documentation and the Blackwell family-specific features post. What
is not documented is how the suffix is represented inside a fat binary entry,
which is the bits-20-and-21 finding here.

**Duplicate entries are refused at creation time.** The nvFatbin documentation
states that a unique identifier "is enforced as only one entry per sm of each
unique identifier". This supports rather than undercuts the work: it is why the
conflicting containers in the corpus have to be built deliberately, which
`entry-precedence.md` already recorded as a negative result.

## Published reverse engineering by others

**The container format is well covered.** Stealthium's write-up of the fatbin
format documents the wrapper, header and entry array in detail, and is the
piece this research started from. On selection it repeats the documented coarse
rule, "for each GPU, prefer a cubin with matching SM version; fall back to PTX
if no match exists", and goes no further. Its entry structure names a
`bin_info` bitfield holding platform, debug and compression bits, and does not
identify the bits that carry the architecture-name suffix or the tie-break.
The authors are explicit that field meanings were inferred by comparing
binaries and are "by no means a standard".

**Open-source parsers enumerate, they do not select.** The fat binary parsers
that exist extract and list entries. None consulted models which entry the
driver would execute.

**The adjacent security work is about something else.** Published CUDA tooling
research is memory-safety fuzzing of the binary utilities, which is where the
2024 `cuobjdump` and `nvdisasm` CVEs came from, and separately GPU side
channels and SASS integrity work. No published analysis of parser-versus-driver
disagreement over which entry is live was found.

## Scope of the claim

Saying the precedence rule is undocumented, without qualification, would
overstate the case: the cubin-beats-PTX part is documented in several places,
and a reader who knows the compatibility guides would catch it immediately. So
`README.md` and `WRITEUP.md` state the documented rule first and locate the
contribution below it.

Searches run 2026-09-14 covering: driver selection and precedence among fat
binary entries; duplicate same-architecture entries and tie-breaking; the entry
flags field and its bits; reverse engineering of libcuda selection logic; and
GPU static-analysis evasion via entry mismatch. Absence of evidence from a
handful of searches is not proof of novelty, so the write-up says "as far as I
can establish" rather than "first".

## Sources

- Ampere Compatibility Guide, NVIDIA, https://docs.nvidia.com/cuda/ampere-compatibility-guide/
- CUDA Compiler Driver NVCC, NVIDIA, https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/index.html
- nvFatbin documentation, NVIDIA, https://docs.nvidia.com/cuda/nvfatbin/index.html
- CUDA Pro Tip: Understand Fat Binaries and JIT Caching, NVIDIA, https://developer.nvidia.com/blog/cuda-pro-tip-understand-fat-binaries-jit-caching/
- Family-Specific Architecture Features, NVIDIA, https://developer.nvidia.com/blog/nvidia-blackwell-and-nvidia-cuda-12-9-introduce-family-specific-architecture-features/
- Inside CUDA Fatbins, Part 1, Stealthium, https://stealthium.io/blog/fatbins-cuda-gpu-binary-formats-part-1
- Demystifying CUDA fat binaries, NVIDIA developer forums, https://forums.developer.nvidia.com/t/demistifying-cuda-fat-binaries/65607
- cudaparsers, https://github.com/vivekpanyam/cudaparsers
