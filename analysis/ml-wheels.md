# The rule against shipped ML wheels

`divergence-matrix.md` establishes the precedence rule on containers built to
conflict, and surveys NVIDIA's own libraries with it. This document asks a
narrower and more awkward question: on GPU code that ordinary people install,
**how much does the undocumented part of the rule actually decide**.

The answer is more limited than the rest of this repository implies, and it is
stated here rather than left for a reader to find.

| | |
|---|---|
| GPU | NVIDIA RTX 2000 Ada Generation Laptop, compute capability 8.9 |
| Driver | 597.16, reached through the WSL2 stub. **Note:** every other document here measures 597.06 |
| Toolkit | CUDA 13.2 |
| Corpora | CUDA 13.2 `lib64`, 21 libraries, 3516 containers; seven GPU wheels, 1125 containers |
| Method | static, except the load test in [On real PyTorch bytes](#on-real-pytorch-bytes) |

## Why not reuse the divergence readings

The matrix compares the driver's rule against `first-match`, `exact-arch` and
`prefer-PTX`. Those three were written for this project, as
`divergence-matrix.md` says plainly: "These three are not measurements of any
shipping product." They exist so the rule has something to be measured against
while it is being established, and on the 41 built containers that job is done
against hardware.

Carrying them onto shipped libraries invites a bad inference. Counting how
often `first-match` disagrees with the rule measures a definition, not a tool.
No scanner claims to implement it, so a large number there is not evidence of
anything beyond how the reading was written. Those counts are in
`divergence-matrix.md` for the toolkit and are deliberately not extended here.

The baseline below is NVIDIA's documentation instead. The Ampere compatibility
guide states that a compatible cubin is used as-is and PTX is compiled
otherwise, and that the runtime finds the "best matching" entry without saying
what that means. So a container is **undetermined by the documentation** when
the documented rule leaves more than one candidate, and the question becomes
how often that happens and what settles it.

## Which level decides

`scripts/decisive_level.py` finds, per container and target, the lowest level
of the hierarchy that had to be consulted. Levels 0 and 1 are the documented
rule; levels 2 to 4 are this repository's findings.

```sh
scripts/decisive_level.py --sm 80,86,89,90 /usr/local/cuda-13.2/lib64
scripts/decisive_level.py --sm 80,86,89,90 <extracted-wheels>/
```

CUDA 13.2 `lib64`, 3516 containers, 24 with ELF architectures not ascending in
file order:

| target | nothing runs | single candidate | 1 kind | 2 architecture | 3 bit 24 | 4 file order |
|---|---|---|---|---|---|---|
| sm_80 | 2171 | 1338 | 7 | 0 | 0 | 0 |
| sm_86 | 2163 | 469 | 1 | 883 | 0 | 0 |
| sm_89 | 2142 | 265 | 0 | **1109** | 0 | 0 |
| sm_90 | 2427 | 1070 | 19 | 0 | 0 | 0 |

Seven GPU wheels, 1125 containers, 340 with ELF architectures not ascending:

| target | nothing runs | single candidate | 1 kind | 2 architecture | 3 bit 24 | 4 file order |
|---|---|---|---|---|---|---|
| sm_80 | 54 | 964 | 107 | 0 | 0 | 0 |
| sm_86 | 54 | 459 | 107 | 505 | 0 | 0 |
| sm_89 | 54 | 420 | 107 | **544** | 0 | 0 |
| sm_90 | 415 | 663 | 26 | 21 | 0 | 0 |

Three things follow, and the second and third are the ones worth being honest
about.

**The documentation is silent on a third of real containers.** At sm_89, 1653
of the 4641 containers across both corpora have more than one candidate of the
winning kind, so the published rule does not name the entry that runs. That is
not a claim about anyone's tool; it is a statement about the documentation's
coverage, and level 2 resolves every one of those cases.

**Level 2 fires only where a major generation holds several targets.** It is
empty at sm_80 and sm_90 and large at sm_86 and sm_89, because the candidate
set can only tie on kind when two shipped architectures sit in the same major
generation at or below the GPU. Generation 8 ships 80, 86 and 89; generations 7
and 9 as shipped here do not. So the undocumented part of the rule is load
bearing on Ampere and Ada consumer parts and idle on an A100 or an H100.

**Levels 3 and 4 decide nothing in either corpus, at any target tested.** Flag
bit 24 and the file-order reversal are measured on hardware and confirmed in
the driver's own code, and they have never yet broken a tie in 4641 shipped
containers. They are mechanism, not present risk, and this document is the
place that says so.

### The part that weakens the result

`decisive_level.py` also reports whether the rule's winner is the first or the
last candidate in file order:

| corpus | target | multi-candidate | winner ≠ first | winner ≠ last |
|---|---|---|---|---|
| toolkit | sm_86 | 883 | 883 | **0** |
| toolkit | sm_89 | 1109 | 1109 | **0** |
| wheels | sm_86 | 505 | 505 | **0** |
| wheels | sm_89 | 544 | 544 | **0** |
| wheels | sm_90 | 21 | 21 | **0** |
| wheels | sm_100 | 47 | 47 | **0** |
| wheels | sm_120 | 47 | 47 | **0** |

In all 3135 multi-candidate cases, across both corpora and five targets, the
entry the rule selects is the **last** candidate of the winning kind in file
order. A reader who took the last compatible entry instead of the first would
be right every time, without knowing anything about architecture proximity.

So the operational payoff of this research on GPU code shipped today reduces to
one correction: among entries of the winning kind, take the last compatible
candidate, not the first. Architecture proximity is the *reason* that works,
and the two are indistinguishable on every file surveyed, because NVIDIA and
every wheel builder here emit architectures in ascending order.

The 340 non-ascending wheel containers do not change this. Their disorder lies
outside the candidate set for the targets tested, in `sm_120a`-before-`sm_100a`
pairs that are no candidate on any GPU here.

Two reasons the distinction still matters, stated as insurance rather than as
risk. Ascending order is a convention of the build, not a property of the
format: `nvcc -gencode` accepts any order, and `divergence-matrix.md` row
"sm_86 ELF B + sm_80 ELF A" is a measured container where the last candidate is
the wrong answer and proximity is the right one. And proximity is what the
driver's ranker actually compares, per `decompiled-selection.md`, so a tool
implementing it is correct for the stated reason rather than by coincidence.

## Reachability, and what a file hash does not say

`scripts/wheel_census.py` reports properties of a container and a target with
no baseline in them at all.

```sh
scripts/wheel_census.py <extracted-wheels>/torch-2.6.0-cp310-cp310-manylinux1_x86_64
```

PyTorch 2.6.0, 448 containers, 3031 entries, all of them ELF, all LZ4:

| target | candidate entries | MB total | MB candidate | MB executed | containers with no entry |
|---|---|---|---|---|---|
| sm_70 | 448 | 4157.0 | 588.9 | 588.9 | 0 |
| sm_75 | 789 | 4157.0 | 1105.2 | 580.7 | 0 |
| sm_80 | 448 | 4157.0 | 663.2 | 663.2 | 0 |
| sm_86 | 896 | 4157.0 | 1327.5 | 664.3 | 0 |
| sm_89 | 896 | 4157.0 | 1327.5 | 664.3 | 0 |
| sm_90 | 448 | 4157.0 | 706.2 | 706.2 | 0 |
| sm_100 | 0 | 4157.0 | 0.0 | 0.0 | **448** |
| sm_120 | 0 | 4157.0 | 0.0 | 0.0 | **448** |

Of 4157 MB of decompressed device code in the wheel, 664 MB executes on an
sm_89 GPU. The rest cannot run there. The same census on xformers 0.0.27.post2
gives 4.7 MB executing out of 54.5 MB.

Across those eight targets, 341 of the 448 containers resolve to five distinct
executed payloads and the remaining 107 to four. One file, one sha256, and the
code that runs out of it is not determined until a target is named. This is the
same point `declared-versus-executed.md` makes with crafted containers, in a
form that needs no crafting: a hash of a shipped library identifies a
container, never the code the GPU ran.

The honest size of this result: both numbers above follow from the compatibility
filter and architecture proximity, which is to say from level 0 and level 2. The
arithmetic is not surprising once you know a wheel ships six architectures. What
the rule adds is which one of the six, stated exactly, and the ability to say
"none of them" when that is the answer.

### A prediction that was not derived here

The sm_100 and sm_120 rows are the useful case. PyTorch 2.6.0 ships 3031 cubin
entries and not one PTX entry, and cubins are compatible only inside their
major generation, so on Blackwell there is no candidate and no JIT fallback: all
448 containers are empty. That is the documented level 0 doing the work, not
this project's findings, and it reproduces the known fact that PyTorch 2.6 does
not run on a Blackwell card and that cu128 wheels from 2.7 onward exist for that
reason.

It is recorded because it is the one check in this document against a ground
truth nobody here invented. It validates the compatibility filter and the
parser's walk, not levels 2 to 4.

xformers gives the milder version: on sm_90 and above the winner flips from the
`sm_80` cubin to the `compute_80` PTX entry, so Hopper runs JIT-compiled PTX
rather than shipped SASS. Also level 0 and level 1, also checkable, also not new
here.

## On real PyTorch bytes

Shipped libraries carry no marker, so nothing in them can be loaded and
observed the way the built corpus is. One claim escapes that limit. Each entry
of one real `libtorch_cuda_linalg.so` container was carved into a standalone
container, its own entry header and payload bytes preserved exactly and only a
fresh 16-byte container header written around it, then loaded with the harness
under `CUDA_CACHE_DISABLE=1`:

| entry | arch | result |
|---|---|---|
| 0 | sm_50 | `CUDA_ERROR_NO_BINARY_FOR_GPU` at `cuModuleLoadData` |
| 1 | sm_60 | `CUDA_ERROR_NO_BINARY_FOR_GPU` at `cuModuleLoadData` |
| 2 | sm_70 | `CUDA_ERROR_NO_BINARY_FOR_GPU` at `cuModuleLoadData` |
| 3 | sm_80 | loads |
| 4 | sm_86 | loads, and is the entry the rule selects |
| 5 | sm_90 | `CUDA_ERROR_NO_BINARY_FOR_GPU` at `cuModuleLoadData` |

Entries 3 and 4 fail later, at `cuModuleGetFunction` looking for the harness's
own `probe` symbol, which is how the module itself can be seen to have loaded.

What this measures is level 0 on bytes nobody here compiled: four of the six
entries are not candidates, and the GPU says so. Which of entries 3 and 4 wins
is **not** measured, because both carry real PyTorch kernels and neither writes
a marker. That remains a claim of the rule, resting on the built corpus.

Carving was checked against the repository's own `single_elf_a.fatbin`, where
the carved output is byte-identical to the original and the readers behave
identically on both.

## The compression family, and one real parser

The flags field carries a compression family rather than one bit, named in
Stealthium's published `BinInfo` enum. `fatbin-entry-kinds.md` records zstd in
a default CUDA 13.2 build and LZ4 in one shipped library, `libcufile`. Across
these two corpora that one entry turns out to be the minority side of a split:

| corpus | containers | zstd entries | LZ4 entries | raw |
|---|---|---|---|---|
| CUDA 13.2 `lib64`, 21 libraries | 3516 | **12780** | 1 | 738 |
| Seven GPU wheels | 1125 | **0** | 3503 | 1462 |

17 of the toolkit's 21 libraries carry at least one zstd entry. Every
compressed entry in every wheel surveyed is LZ4. The two ecosystems use
opposite schemes.

This has a consequence for a real, named parser. Datadog's `pkg/gpu/cuda`, at
the commit `tools/fetch.sh` pins, decides to decompress on
`UncompressedPayloadSize != 0` and then calls `lz4.UncompressBlock`
unconditionally (`fatbin.go`, the compressed branch). There is no zstd path in
it. Built from the same cubin, one `fatbinary` flag apart:

```
$ fatbinary --create=z_raw.fatbin  --image3=kind=elf,sm=89,file=build/variant_a.cubin
$ fatbinary --create=z_zstd.fatbin --image3=kind=elf,sm=89,file=build/variant_a.cubin --compress-all

z_raw    ELF sm_89  3112 bytes  uncompressed  sha256 69e6171ebbd6b307
  dd_probe: kernel "probe", 384 bytes
z_zstd   ELF sm_89   848 bytes  zstd          sha256 69e6171ebbd6b307
  dd_probe: parse error: failed to parse fatbin data: failed to decompress
            fatbin payload: lz4: invalid source or destination buffer too short
```

Same architecture, same decompressed payload, same hash. One compression
setting turns a parsed kernel into an error that fails the whole container, and
with it every kernel the container held.

Three qualifications belong with that, in the same breath. Datadog's parser is
GPU observability and not a security control, as `prior-art.md` says: a
mis-parse costs a metric. The failure is loud rather than silent, which is the
better of the two ways to be wrong. And it does not affect the ML wheels at all,
since those are LZ4 — it affects NVIDIA's own libraries, which is the opposite
of what the supply-chain framing would predict.

`cuobjdump` handles both. On `libtorch_cuda_linalg.so` it lists 642 entries,
exactly matching the parser's count, decompressing LZ4 without complaint. Its
limitation is the documented one: it lists all 642 and marks none as the entry
that would run.

What this demonstrates is `fatbin-entry-kinds.md`'s point that decompression
must be keyed on the compression family and not on the entry kind or a single
bit, with a real implementation showing what the other choice costs.

## Negative results

Recorded because they bound the claims above.

- **No naturally occurring hash collision.** Across all 1125 wheel containers,
  no naive declared-bytes hash covers more than one real payload, and no
  payload is split across several naive hashes. The collision in
  `declared-versus-executed.md` is a property of crafted containers. What does
  occur naturally is 42 xformers PTX entries whose driver read extent differs
  from their declared `padded_payload_size`.
- **Triton ships no fat binaries.** Zero containers across ten shared objects
  in `triton` 3.2.0. It generates device code at runtime, so a file reader has
  nothing to read and nothing to get wrong.
- **flash-attn diverges on nothing.** 84 containers, every one `sm_80 + sm_90`,
  where the first candidate is also the entry the rule selects on sm_89.
- **All 1125 containers parse with no structural note**, extending the
  clean-parse count from the toolkit's 3516 to 4641.
- **Levels 3 and 4 never fire**, as above.

## The wheels

Downloaded from PyPI, except flash-attn, which publishes prebuilt wheels only
through its GitHub releases. Each extracted into its own directory and read,
never installed or executed.

| package | version | wheel tag | containers |
|---|---|---|---|
| torch | 2.6.0 | `cp310-cp310-manylinux1_x86_64` | 448 |
| vllm | 0.11.2 | `cp38-abi3-manylinux1_x86_64` | 509 |
| flash-attn | 2.7.4.post1 | `cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64` | 84 |
| bitsandbytes | 0.42.0 | `py3-none-any` | 60 |
| xformers | 0.0.27.post2 | `cp310-cp310-manylinux2014_x86_64` | 21 |
| cupy-cuda12x | 14.2.0 | `cp310-cp310-manylinux2014_x86_64` | 3 |
| triton | 3.2.0 | `cp310-cp310-manylinux_2_17_x86_64` | 0 |

```sh
pip download --no-deps --only-binary=:all: \
  --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 \
  --platform manylinux1_x86_64 --python-version 310 \
  --implementation cp --abi cp310 -d wheels/ \
  torch==2.6.0 vllm==0.11.2 bitsandbytes==0.42.0 \
  xformers==0.0.27.post2 cupy-cuda12x==14.2.0 triton==3.2.0

curl -sSL -o wheels/flash_attn.whl \
  https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl

for w in wheels/*.whl; do unzip -qq -o "$w" -d "extracted/$(basename "$w" .whl)"; done
scripts/decisive_level.py --sm 80,86,89,90 extracted/
scripts/wheel_census.py extracted/torch-2.6.0-cp310-cp310-manylinux1_x86_64
```

`bitsandbytes` resolves to 0.42.0 under those platform tags; later releases
publish `manylinux_2_24` wheels that the tag set above does not match.

## Limits

Every count is for the targets named. Level 2's prominence at sm_86 and sm_89
and its absence at sm_80 and sm_90 is a fact about which architectures these
particular builds ship, not a general property, and a different wheel set
would move it.

The wheel measurements are static. Nothing was loaded except the six carved
PyTorch entries, and no wheel was installed or run. Where a count compares
against the precedence rule rather than against hardware, that rule's standing
comes from the 41 measured cases in `divergence-matrix.md` and from the driver
code in `decompiled-selection.md`, not from this document.

The driver here is 597.16 while the rest of this repository measures 597.06. The
load results above agree with the rule on the newer build, which is one data
point for level 0 surviving a driver update and not a re-validation of anything
else.

One defect found while doing this, in this repository rather than in anyone
else's: `tools/dd_probe/main.go` prints `usage: dd_probe <fatbin> [sm]` but
assigns `sm := uint32(89)` and never reads `os.Args[2]`, so the target argument
is silently ignored. Every published figure using that probe was taken at
sm_89, which is the hardcoded value, so the numbers in the top-level README
stand. It is noted here because the experiments above were briefly run against
the wrong target before the source was read.
