#!/usr/bin/env python3
"""Apply the precedence rule to shipped libraries, not just to built test cases.

The divergence matrix is built from containers made deliberately to conflict.
This asks a different question: on ordinary NVIDIA-shipped libraries, which
nobody crafted, does a conventional reading pick the entry the GPU runs?

    scripts/survey_libs.py [--sm 89] [--max-bytes N] <dir-or-file> ...

Defaults to the CUDA toolkit's own lib64, which is the closest thing to hand to
"binaries I did not compile".
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fatbin_entry_selection as fp

READINGS = (("first-match", fp.naive_first_match),
            ("exact-arch", fp.naive_exact_arch),
            ("prefer-PTX", fp.naive_prefer_ptx))


def survey_file(path, sm):
    """Return (containers, multi_entry, {reading: wrong}, notes) for one file."""
    wrong = {name: 0 for name, _ in READINGS}
    total = multi = notes = 0
    for _desc, off, blob in fp.find_containers(path):
        try:
            hdr, entries = fp.parse_container(blob, off)
        except ValueError:
            notes += 1
            continue
        if not entries:
            continue
        total += 1
        if len(entries) > 1:
            multi += 1
        # Count container-level notes as well as entry-level ones. Counting
        # only the entries would let a container whose own header is the
        # problem, a truncated fat_size or an entry outside it, pass as clean.
        if any(e.notes for e in entries) or getattr(hdr, "notes", None):
            notes += 1
        winner = fp.would_execute(entries, sm)
        target = winner.index if winner else None
        for name, fn in READINGS:
            got = fn(entries, sm)
            if (got.index if got else None) != target:
                wrong[name] += 1
    return total, multi, wrong, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*",
                    default=["/usr/local/cuda-13.2/lib64"])
    ap.add_argument("--sm", type=int, default=89)
    # No size limit by default. An earlier default of 60 MB silently dropped
    # nine libraries, libcublasLt among them, which alone holds 2617 of the
    # toolkit's 3516 containers, so the survey described a fraction of the
    # toolkit while the write-up claimed all of it. Pass --max-bytes to trade
    # coverage for speed, and say so if you quote the result.
    ap.add_argument("--max-bytes", type=int, default=0,
                    help="skip files larger than this many bytes; 0, the "
                         "default, reads every library, which takes minutes")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        if os.path.isdir(p):
            files += [f for f in sorted(glob.glob(os.path.join(p, "*.so.*.*")))
                      if not os.path.islink(f)]
        else:
            files.append(p)

    header = f"{'library':<34}{'cont':>5}{'multi':>6}"
    for name, _ in READINGS:
        header += f"{name:>12}"
    header += f"{'notes':>7}"
    print(header)

    grand = {name: 0 for name, _ in READINGS}
    g_total = g_multi = g_notes = g_files = 0
    for path in files:
        if args.max_bytes and os.path.getsize(path) > args.max_bytes:
            continue
        try:
            total, multi, wrong, notes = survey_file(path, args.sm)
        except (ValueError, ImportError):
            continue
        if not total:
            continue
        g_files += 1
        line = f"{os.path.basename(path):<34}{total:>5}{multi:>6}"
        for name, _ in READINGS:
            line += f"{wrong[name]:>12}"
            grand[name] += wrong[name]
        print(line + f"{notes:>7}")
        g_total += total
        g_multi += multi
        g_notes += notes

    line = f"{f'TOTAL, {g_files} libraries':<34}{g_total:>5}{g_multi:>6}"
    for name, _ in READINGS:
        line += f"{grand[name]:>12}"
    print(line + f"{g_notes:>7}")
    print()
    print(f"Target sm_{args.sm}. 'cont' counts fat binary containers, 'multi' "
          f"those with more than one entry.")
    print("Each reading's column counts containers where it names an entry "
          "other than the one the driver's rule selects.")
    print("'notes' counts containers where the parser flagged a structural "
          "problem; zero means every shipped container parsed cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
