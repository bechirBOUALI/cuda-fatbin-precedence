#!/usr/bin/env python3
"""Reachability, target sensitivity and compression family of shipped GPU code.

Three measurements whose answers do not depend on any reading this project
invented. Each is a property of a container and a target.

  REACHABILITY     how much of the device code in a file could execute at all
                   on one GPU, in entries and in decompressed bytes.
  TARGET SENSITIVITY  the same container across GPUs: how many distinct
                   payloads one file resolves to, which is what a hash of the
                   file cannot tell you.
  COMPRESSION      the compression family per entry. The flags carry a family
                   rather than one bit, and the two corpora surveyed in
                   analysis/ml-wheels.md use opposite ones, so a parser keyed
                   on a single scheme reads one of them as garbage.

    scripts/wheel_census.py [--sm 89] [--targets 70,80,89,90] <dir-or-file> ...

Directories are searched for shared objects, following symlinked directories.
"""

import argparse
import collections
import glob
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fatbin_entry_selection as fp


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for pat in ("*.so", "*.so.*"):
                files += [f for f in sorted(glob.glob(os.path.join(p, "**", pat),
                                                      recursive=True))
                          if os.path.isfile(f) and not os.path.islink(f)]
        else:
            files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--sm", type=int, default=89)
    ap.add_argument("--targets", default="70,75,80,86,89,90,100,120")
    args = ap.parse_args()
    targets = [int(s) for s in args.targets.split(",")]

    rows = []
    for path in collect(args.paths):
        try:
            found = list(fp.find_containers(path))
        except Exception:
            continue
        for _desc, off, blob in found:
            try:
                _hdr, entries = fp.parse_container(blob, off)
            except ValueError:
                continue
            if entries:
                rows.append(entries)

    if not rows:
        print("no fat binary containers found")
        return 1

    # ---- compression family, and kind ---------------------------------
    fam = collections.Counter()
    kinds = collections.Counter()
    notes = 0
    for entries in rows:
        for e in entries:
            kinds[e.kind_name] += 1
            if e.lz4:
                fam["lz4"] += 1
            elif e.compressed:
                fam["zstd"] += 1
            elif e.stored_compressed:
                fam["other"] += 1
            else:
                fam["raw"] += 1
            if e.notes:
                notes += 1

    print(f"{len(rows)} containers, {sum(kinds.values())} entries")
    print("  kinds:       " + "  ".join(f"{k}={v}" for k, v in kinds.items()))
    print("  compression: " + "  ".join(f"{k}={v}" for k, v in fam.items()))
    print(f"  entries carrying a parser note: {notes}")
    print()

    # ---- reachability, per target ---------------------------------------
    print(f"{'target':>8}{'entries':>9}{'candidate':>11}"
          f"{'MB total':>10}{'MB cand':>10}{'MB exec':>10}{'no entry':>10}")
    for sm in targets:
        n_cand = n_entries = 0
        b_total = b_cand = b_exec = 0
        dead = 0
        for entries in rows:
            win = fp.would_execute(entries, sm)
            if win is None:
                dead += 1
            else:
                b_exec += len(win.payload or b"")
            for e in entries:
                n_entries += 1
                size = len(e.payload or b"")
                b_total += size
                if fp.arch_compatible(e, sm):
                    n_cand += 1
                    b_cand += size
        print(f"{'sm_' + str(sm):>8}{n_entries:>9}{n_cand:>11}"
              f"{b_total / 1e6:>10.1f}{b_cand / 1e6:>10.1f}"
              f"{b_exec / 1e6:>10.1f}{dead:>10}")
    print()
    print("'MB total' is decompressed device code in the file; 'MB exec' is "
          "what the one\nselected entry per container amounts to. The gap is "
          "code that cannot run on\nthat target.")
    print()

    # ---- target sensitivity ---------------------------------------------
    distinct = collections.Counter()
    for entries in rows:
        seen = set()
        for sm in targets:
            win = fp.would_execute(entries, sm)
            if win is not None:
                seen.add(hashlib.sha256(win.payload or b"").hexdigest())
        distinct[len(seen)] += 1
    print("distinct executed payloads per container, across "
          f"{len(targets)} targets:")
    for n in sorted(distinct):
        print(f"  {n} payload(s): {distinct[n]} containers")
    print("A container resolving to more than one payload is one whose code "
          "a hash of the\nfile does not identify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
