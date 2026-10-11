#!/usr/bin/env python3
"""Which level of the hierarchy actually decides the winner, in shipped code.

`divergence_matrix.py` asks how often three readings disagree with the rule.
Those readings were written for this project, so the answer depends on how they
were defined and says nothing about anyone's tool. This script asks a question
whose baseline is NVIDIA's own documentation instead.

NVIDIA documents two things bearing on selection: a compatible cubin is
preferred over PTX, and the runtime finds the "best matching" entry without
saying what that means. So for each container and target, find the lowest level
that had to be consulted:

  0  the compatibility filter leaves one candidate, or none at all.
  1  several candidates of different kinds, settled by cubin-over-PTX.
     Levels 0 and 1 are the documented rule. Everything below is not.
  2  several candidates of the SAME kind, settled by architecture proximity.
     The documentation stops short of this: "best matching" does not say
     whether a nearer architecture beats a further one.
  3  same kind and architecture, settled by flag bit 24.
  4  same kind, architecture and bit 24, settled by file order, whose
     direction reverses between cubin and PTX.

The count at levels 2 to 4 is the measure of whether this project's findings
change any real answer, and the `winner_not_last_candidate` column is the
measure of whether level 2's *direction* can be told apart from simply taking
the last compatible entry. On every corpus surveyed so far that column is
zero, which is reported rather than buried: see analysis/ml-wheels.md.

    scripts/decisive_level.py [--sm 89[,86,...]] <dir-or-file> ...

A directory argument is searched for shared objects, following symlinked
directories, since a CUDA install reaches lib64 through one.
"""

import argparse
import collections
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fatbin_entry_selection as fp

LEVELS = ("0-nothing-runs", "0-single-candidate", "1-kind",
          "2-architecture", "3-flag-bit-24", "4-file-order")


def candidate_pool(entries, sm):
    return [e for e in entries
            if fp.arch_compatible(e, sm) and e.kind in fp.KIND_RANK]


def decide(entries, sm):
    """Return (level, winner, same_kind_candidates) for one container."""
    pool = candidate_pool(entries, sm)
    if not pool:
        return "0-nothing-runs", None, []
    if len(pool) == 1:
        return "0-single-candidate", pool[0], pool

    top_rank = max(fp.KIND_RANK[e.kind] for e in pool)
    kind_group = [e for e in pool if fp.KIND_RANK[e.kind] == top_rank]
    if len(kind_group) == 1:
        return "1-kind", kind_group[0], kind_group

    top_arch = max(e.arch for e in kind_group)
    arch_group = [e for e in kind_group if e.arch == top_arch]
    if len(arch_group) == 1:
        return "2-architecture", arch_group[0], kind_group

    # Bit 24 is only consulted for ELF entries, and only separates them when
    # some but not all of the tied group carry it.
    if arch_group[0].kind == fp.KIND_ELF:
        clean = [e for e in arch_group if not e.deprioritised]
        if clean and len(clean) < len(arch_group):
            return "3-flag-bit-24", clean[0], kind_group

    return "4-file-order", fp.would_execute(entries, sm), kind_group


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            # Follow symlinked directories: a CUDA install reaches lib64
            # through one, so a plain walk finds nothing there.
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
    ap.add_argument("--sm", default="89",
                    help="comma-separated targets, default 89")
    args = ap.parse_args()
    targets = [int(s) for s in args.sm.split(",")]

    counts = collections.defaultdict(collections.Counter)
    not_last = collections.Counter()
    not_first = collections.Counter()
    multi = collections.Counter()
    nonascending = containers = 0

    for path in collect(args.paths):
        try:
            found = list(fp.find_containers(path))
        except Exception:
            # A shared object with no .nv_fatbin carries no GPU code. Not an
            # error, and not a container either.
            continue
        for _desc, off, blob in found:
            try:
                _hdr, entries = fp.parse_container(blob, off)
            except ValueError:
                continue
            if not entries:
                continue
            containers += 1
            archs = [e.arch for e in entries if e.kind == fp.KIND_ELF]
            if archs != sorted(archs):
                nonascending += 1
            for sm in targets:
                level, win, kind_group = decide(entries, sm)
                counts[sm][level] += 1
                if win is None or len(kind_group) < 2:
                    continue
                multi[sm] += 1
                if win.index != kind_group[-1].index:
                    not_last[sm] += 1
                if win.index != kind_group[0].index:
                    not_first[sm] += 1

    print(f"{containers} containers, {nonascending} of them with ELF "
          f"architectures not ascending in file order")
    print()
    head = f"{'target':>8}" + "".join(f"{l.split('-',1)[1][:11]:>13}" for l in LEVELS)
    print(head)
    for sm in targets:
        print(f"{'sm_' + str(sm):>8}" +
              "".join(f"{counts[sm][l]:>13}" for l in LEVELS))
    print()
    print(f"{'target':>8}{'multi-cand':>13}{'win!=first':>13}{'win!=last':>13}")
    for sm in targets:
        print(f"{'sm_' + str(sm):>8}{multi[sm]:>13}"
              f"{not_first[sm]:>13}{not_last[sm]:>13}")
    print()
    print("Levels 0 and 1 are NVIDIA's documented rule. Levels 2 to 4 are not "
          "documented;\nthe count there is how often the undocumented part of "
          "the rule decides the outcome.")
    print("'multi-cand' counts containers with more than one candidate of the "
          "winning kind,\nwhich are exactly the containers the documentation "
          "leaves undetermined.")
    print("'win!=last' is the only column that can distinguish architecture "
          "proximity from\nsimply taking the last compatible entry. Zero means "
          "the two agree on this corpus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
