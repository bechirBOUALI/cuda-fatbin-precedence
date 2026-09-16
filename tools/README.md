# The three readers, run for yourself

The table in the top-level README compares what three real tools report against
what the GPU ran. This directory is how that table is produced. Nothing is
vendored: both probes call the upstream parsers, fetched at pinned revisions.

```sh
./tools/fetch.sh     # clone at the pins, build both probes
./tools/run.sh       # run every reader over the same containers
```

`fetch.sh` needs `cargo`, `go` 1.24 or newer, `gcc`, `git` and `objcopy`.
`run.sh` additionally needs `cuobjdump` from the CUDA toolkit, and the corpus
built by `make -C src/kernels`.

## The pins

| tool | revision |
|---|---|
| ZLUDA | `9c8b43f242985150f86a7f485218b7b82c3e96ca` |
| Datadog agent | `89c7030e39d45b068f3b7df96f3570b11c860431` |

## What each probe does

**`zluda_probe`** links ZLUDA's own `dark_api::fatbin` crate and iterates the
entries with it. The selection is the two steps `zluda/src/impl/module.rs`
performs, kept literal:

```rust
if file.header.kind != FatbinFileHeader::HEADER_KIND_PTX { return; }
...
ptx_modules.iter().rev()   // TODO: actually sort by SM
```

Cubins are discarded, PTX entries are walked backwards, and the last one wins.
ZLUDA additionally requires the winning PTX to parse before accepting it. That
step needs its compiler crates and is not reproduced here; on every container
in the table the answer is the same either way, since each holds at most one
parseable PTX entry.

Two build details worth knowing, because both look odd and neither changes what
is measured. ZLUDA's `cuda_types` crate links the ROCm runtime, which this
probe never calls, so `fetch.sh` writes empty stub libraries to satisfy the
linker rather than requiring a ROCm install. And the probe reads a raw
container directly through `FatbinFileIterator`, because ZLUDA's entry point
above it expects the registration wrapper that a host binary carries.

**`dd_probe`** calls `cuda.ParseFatbinFromELFFilePath` from the Datadog agent's
`pkg/gpu/cuda`. That parser reads `.nv_fatbin` sections out of an ELF rather
than a raw container, which is how it meets fat binaries in a deployed agent,
so the probe wraps the container in an object file with `objcopy` first and
hands that over. Only `pkg/gpu/cuda` and `pkg/util/safeelf` are checked out;
resolving the whole agent module graph pulls gigabytes and is not needed to
read a fat binary.

## What the comparison is, and is not

Neither tool is a security control. Datadog's parser is GPU observability,
where a mis-parse costs a metric rather than a gate. ZLUDA is a compatibility
layer. They are here because they are the real, named, open-source instances of
the pattern a scanner would be built from, and because both are wrong about
which entry executes in ways that are easy to reproduce.

The driver column of the published table does not come from this directory. It
comes from `build/loader`, which loads each container on the GPU and reads back
the marker the kernel wrote. `scripts/divergence_matrix.py` is that measurement.
