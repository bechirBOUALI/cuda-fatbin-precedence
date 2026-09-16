#!/bin/sh
# Run every reader over the same containers and print the comparison table.
#
# The driver column is not produced here: it comes from build/loader, which
# needs the GPU. scripts/divergence_matrix.py is the measured version.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
build="$root/build"
CUOBJDUMP=${CUOBJDUMP:-/usr/local/cuda-13.2/bin/cuobjdump}
zluda="$here/zluda_probe/target/release/zluda_probe"
dd="$here/dd_probe/dd_probe"

for p in "$zluda" "$dd"; do
    [ -x "$p" ] || { echo "missing $p, run $here/fetch.sh first" >&2; exit 1; }
done

cases=${*:-"elf80a_elf86b ptxa_elfb ptx_ab"}

printf '%-18s | %-22s | %-24s | %-26s | %s\n' \
       container cuobjdump "datadog pkg/gpu/cuda" zluda "this parser"
for c in $cases; do
    f="$build/$c.fatbin"
    [ -f "$f" ] || { echo "missing $f, run make -C src/kernels" >&2; exit 1; }
    n=$("$CUOBJDUMP" -lelf -lptx "$f" 2>/dev/null | grep -c "file" || true)
    ours=$(python3 "$root/scripts/fatbin_entry_selection.py" "$f" \
           | awk '/EXECUTES/{print "entry " $1}')
    printf '%-18s | %-22s | %-24s | %-26s | %s\n' \
           "$c" "lists $n, marks none" \
           "$(LD_LIBRARY_PATH="$here/upstream/stubs" "$dd" "$f")" \
           "$(LD_LIBRARY_PATH="$here/upstream/stubs" "$zluda" "$f")" \
           "${ours:-nothing runs}"
done
