#!/bin/sh
# Fetch the two upstream tools at pinned revisions and build the probes.
#
# Nothing is vendored into this repository. Both probes call the upstream
# parsers; see README.md in this directory for what each one does and does not
# reproduce.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
up="$here/upstream"
ZLUDA_PIN=9c8b43f242985150f86a7f485218b7b82c3e96ca
DATADOG_PIN=89c7030e

mkdir -p "$up"

# ZLUDA, built from a clone because the probe links its crates by path.
if [ ! -d "$up/zluda/.git" ]; then
    git clone --filter=blob:none --no-checkout https://github.com/vosen/ZLUDA.git "$up/zluda"
fi
git -C "$up/zluda" checkout -q "$ZLUDA_PIN"

# ZLUDA's cuda_types crate links the ROCm runtime, which this probe never
# calls. Empty stubs satisfy the linker without installing ROCm.
mkdir -p "$up/stubs"
for lib in amdhip64 rocblas hipblaslt MIOpen rocsparse rocm_smi64; do
    [ -f "$up/stubs/lib$lib.so" ] || echo "" | gcc -shared -x c - -o "$up/stubs/lib$lib.so"
done

sed "s|@ZLUDA@|$up/zluda|g" "$here/zluda_probe/Cargo.toml.in" \
    > "$here/zluda_probe/Cargo.toml"
( cd "$here/zluda_probe" && RUSTFLAGS="-L $up/stubs" cargo build --release )

# Datadog's parser lives in a monorepo, so only the two packages the probe
# imports are checked out. Resolving the whole module graph instead pulls
# gigabytes and is not needed to read a fat binary.
if [ ! -d "$up/ddagent/.git" ]; then
    git clone --filter=blob:none --sparse \
        https://github.com/DataDog/datadog-agent.git "$up/ddagent"
fi
git -C "$up/ddagent" sparse-checkout set --cone pkg/gpu/cuda pkg/util/safeelf
git -C "$up/ddagent" checkout -q "$DATADOG_PIN"
# go.mod and go.sum sit outside the sparse cone, so materialise them directly.
git -C "$up/ddagent" show "$DATADOG_PIN:go.mod" > "$up/ddagent/go.mod"
git -C "$up/ddagent" show "$DATADOG_PIN:go.sum" > "$up/ddagent/go.sum"

# go.mod.in is the template; go.mod is generated and gitignored.
sed "s|@DDAGENT@|$up/ddagent|g" "$here/dd_probe/go.mod.in" > "$here/dd_probe/go.mod"
( cd "$here/dd_probe" && GOFLAGS=-mod=mod go build -o dd_probe . )

echo "probes built:"
echo "  $here/zluda_probe/target/release/zluda_probe"
echo "  $here/dd_probe/dd_probe"
