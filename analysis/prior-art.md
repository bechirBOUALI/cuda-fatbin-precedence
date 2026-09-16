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

**The threat model is Stealthium's, and they state it plainly.** The same
write-up names the attack this work sits on top of: "Legitimate code for SM_80
(A100 GPUs), Malicious code for SM_89 (L40 GPUs), Different behaviour for
SM_86 (RTX 3090) versus SM_90 (H100)". It adds that "no published research has
examined the risk of architecture-specific malicious payloads within fatbins".
So the idea that a fat binary can carry different code per architecture, and
that this is a security problem, is theirs and was published first. What is not
there is the rule: their account of selection is the two-tier "matching SM
version, else PTX", with nothing on compatible-but-not-exact cubins, the
tie-breaks, or the flag bits.

Their product intercepts several CUDA APIs in real time with eBPF uprobes,
`cuModuleLoadData()` among them, capturing the complete fatbin, the process
context, every contained PTX and cubin, hashes of the individual kernels, the
architecture targets and toolkit versions, and the compression and binary
metadata. That vantage point beats reading a file section, since it also
catches containers assembled on the heap, and it is not confined to the
container: selection happens inside `libcuda` after the load, so a hook set
reaching past `cuModuleLoadData()` into the calls that follow is in a position
to attribute the code that actually ran. Dynamic instrumentation is simply
better placed for that question than anything reading the file, and this
write-up should not be read as saying otherwise. What does not follow from it
is the rule: it needs the code to execute, on a host you control, while it
runs, and their published account of selection remains the two-tier "matching
SM version, else PTX".

**Runtime observation is a solved problem, officially and otherwise.** CUPTI's
`CUPTI_CBID_RESOURCE_MODULE_LOADED` hands a profiler the payload the driver
selected rather than the container. Measured here: a 6368-byte container
holding two sm_89 cubins yields 3112 bytes, a bare ELF, carrying only the
winning kernel's marker, matching the driver on every case tried. It needs the
code to execute, and it returns SASS without provenance, since a 640-byte
PTX-only container yields a 3112-byte ELF found nowhere in the input. But any
claim that nothing can observe the selected entry would be wrong.

**Open-source parsers enumerate, they do not select.** The fat binary parsers
that exist extract and list entries. The clearest case is ZLUDA, the one
project that must consume real fat binaries to function: it discards every
cubin, `if file.header.kind != HEADER_KIND_PTX { return; }`, then walks PTX
entries in reverse behind a literal `// TODO: actually sort by SM`. The rule is
not merely undocumented, it is unimplemented outside NVIDIA.

**The same attack class is established on another platform.** Patrick Wardle
showed in 2024 that macOS's `macho_best_slice()` can disagree with what `dyld`
actually runs, and that security tools trusting it can be evaded. The contrast
is the useful part: Apple ships an API whose whole job is naming the slice that
will run, and CUDA ships no equivalent call.

**The adjacent security work is about something else.** Published CUDA tooling
research is memory-safety fuzzing of the binary utilities, which is where the
2024 `cuobjdump` and `nvdisasm` CVEs came from, and separately GPU side
channels and SASS integrity work. No published analysis of parser-versus-driver
disagreement over which entry is live was found.

## Scope of the claim

Saying the precedence rule is undocumented, without qualification, would
overstate the case: the cubin-beats-PTX part is documented in several places,
and a reader who knows the compatibility guides would catch it immediately. So
`README.md` and `SELECTION-RULE.md` state the documented rule first and locate the
contribution below it.

Two further claims had to be narrowed. The threat model is not this work's, it
is Stealthium's and was published first, so what is claimed here is the rule
rather than the idea. And "nothing can tell which entry runs" is false, since
CUPTI reports exactly that at runtime and eBPF instrumentation across the CUDA
API is positioned to attribute it as well, so the claim is narrowed to
determining it from the file without executing it.

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
- ZLUDA, `zluda/src/impl/module.rs`, https://github.com/vosen/ZLUDA
- CUpti_ModuleResourceData, NVIDIA, https://docs.nvidia.com/cupti/api/structCUpti__ModuleResourceData.html
- Patrick Wardle, "Fool Us Once, Shame On You..." on macOS universal binary
  slice selection and `macho_best_slice()`, Objective-See blog, 2024,
  https://objective-see.org/blog.html
- Datadog agent, `pkg/gpu/cuda`, https://github.com/DataDog/datadog-agent
