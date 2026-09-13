#!/usr/bin/env python3
"""Build the divergence matrix: what each scanner reports against what the GPU runs.

For every container in the corpus this runs three things and compares them:

  * the driver, by loading the container and reading back the marker the kernel
    wrote, which is ground truth for what executed;
  * two conventional static readings, first-match and prefer-PTX;
  * `fatbin_parser.would_execute`, which implements the driver's own rule.

A row where a static reading disagrees with the driver is a divergence: the
scanner is describing code the GPU does not run. The final column is the fix,
and it should agree with the driver on every row.

    scripts/divergence_matrix.py [--markdown]

Needs the corpus built (`make -C src/kernels`) and the loader
(`make -C src/harness`), and a GPU, since the driver column is measured rather
than predicted.
"""

import argparse
import hashlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fatbin_parser as fp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
LOADER = os.path.join(BUILD, "loader")

# The corpus, in the order the rules are argued: controls, then level 3, then
# level 2, then level 1, then the flag-bit cases. `env` is applied on top of
# the environment for that row only.
CASES = [
    # Controls: the harness agrees with every reading, so the disagreements
    # further down are not artefacts of the measurement.
    ("single_elf_a.fatbin",   "control, one sm_89 ELF",                      {}),
    ("elf_ab.fatbin",         "ELF A + ELF B, both sm_89",                   {}),
    ("elf_ba.fatbin",         "ELF B + ELF A, both sm_89",                   {}),
    # Level 3, and the direction reverses with the kind.
    ("ptx_ab.fatbin",         "PTX A + PTX B, both compute_89",              {}),
    ("ptx_ba.fatbin",         "PTX B + PTX A, both compute_89",              {}),
    # Level 2, architecture proximity, order-independent.
    ("elf89a_elf86b.fatbin",  "sm_89 ELF A + sm_86 ELF B",                   {}),
    ("elf86b_elf89a.fatbin",  "sm_86 ELF B + sm_89 ELF A",                   {}),
    ("elf80a_elf86b.fatbin",  "sm_80 ELF A + sm_86 ELF B, neither exact",    {}),
    ("elf86b_elf80a.fatbin",  "sm_86 ELF B + sm_80 ELF A, neither exact",    {}),
    ("elf_three.fatbin",      "sm_75 + sm_80 + sm_86 ELF, ascending",        {}),
    ("elf_three_rev.fatbin",  "sm_86 + sm_80 + sm_75 ELF, descending",       {}),
    ("single_elf75.fatbin",   "control, one sm_75 ELF, wrong generation",     {}),
    ("elf75a_ptx75b.fatbin",  "sm_75 ELF A + compute_75 PTX B",              {}),
    ("ptx75b_elf75a.fatbin",  "compute_75 PTX B + sm_75 ELF A",              {}),
    # Level 1, kind, which outranks both of the above.
    ("ptxa_elfb.fatbin",      "compute_89 PTX A + sm_89 ELF B",              {}),
    ("elfb_ptxa.fatbin",      "sm_89 ELF B + compute_89 PTX A",              {}),
    ("elf86a_ptx89b.fatbin",  "sm_86 ELF A + compute_89 PTX B",              {}),
    ("ptx89b_elf86a.fatbin",  "compute_89 PTX B + sm_86 ELF A",              {}),
    # Selection is not decided by the container alone.
    ("flag20_elfa.fatbin",    "one sm_89 ELF, flags bit 20 set",             {}),
    ("flag21_elfa.fatbin",    "one sm_89 ELF, flags bit 21 set",             {}),
    ("flag24_elfa.fatbin",    "one sm_89 ELF, flags bit 24 set",             {}),
    ("b24_on_first.fatbin",   "ELF A + ELF B, bit 24 on A only",             {}),
    ("b24_on_second.fatbin",  "ELF A + ELF B, bit 24 on B only",             {}),
    ("ptxa_elfb.fatbin",      "PTX A + ELF B, CUDA_FORCE_PTX_JIT=1",
     {"CUDA_FORCE_PTX_JIT": "1"}),
]

NOTHING = "nothing runs"


def variant_table():
    """sha256 of each standalone device image -> variant name.

    Identifying entries by payload hash rather than by position means the
    mapping cannot drift if the build order changes.
    """
    table = {}
    for name in ("variant_a", "variant_b"):
        for suffix in (".cubin", "_sm86.cubin", "_sm80.cubin", "_sm75.cubin"):
            path = os.path.join(BUILD, name + suffix)
            if os.path.exists(path):
                digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
                table[digest] = name
    return table


CUBIN_BY_HASH = variant_table()


def identify(entry):
    """Name the variant an entry carries.

    Cubins are matched by payload hash against the standalone files. PTX cannot
    be, because fatbinary rewrites the text slightly, so the marker constant is
    read out of the source instead: 0xAAAA is 43690 and 0xBBBB is 48059.
    """
    if not entry.payload:
        return "?"
    if entry.payload.startswith(b"\x7fELF"):
        return CUBIN_BY_HASH.get(entry.payload_sha256, "unknown cubin")
    text = entry.payload.decode("utf-8", "replace")
    if "43690" in text:
        return "variant_a"
    if "48059" in text:
        return "variant_b"
    return "unknown ptx"


def run_driver(path, env_extra):
    """Ground truth: load the container and report which marker came back.

    CUDA_CACHE_DISABLE=1 is not optional. Without it a cached JIT result from an
    earlier run can be served back and recorded as a fresh selection decision.
    """
    env = dict(os.environ, CUDA_CACHE_DISABLE="1", **env_extra)
    proc = subprocess.run([LOADER, path], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        if "CUDA_ERROR_NO_BINARY_FOR_GPU" in proc.stderr:
            return NOTHING
        return f"load error ({proc.returncode})"
    for token in ("variant_a", "variant_b"):
        if token in proc.stdout:
            return token
    return "unrecognised"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sm", type=int, default=89)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(LOADER):
        sys.exit("loader not built: run make -C src/harness")

    rows = []
    wrong = {"first-match": 0, "exact-arch": 0, "prefer-PTX": 0,
             "precedence-aware": 0}
    for filename, description, env_extra in CASES:
        path = os.path.join(BUILD, filename)
        if not os.path.exists(path):
            sys.exit(f"missing {path}: run make -C src/kernels")

        policy = "force-ptx-jit" if "CUDA_FORCE_PTX_JIT" in env_extra else "default"
        _hdr, entries = fp.parse_container(open(path, "rb").read(), 0)

        def name(entry):
            return identify(entry) if entry else NOTHING

        driver = run_driver(path, env_extra)
        first = name(fp.naive_first_match(entries, args.sm))
        exact = name(fp.naive_exact_arch(entries, args.sm))
        ptxish = name(fp.naive_prefer_ptx(entries, args.sm))
        aware = name(fp.would_execute(entries, args.sm, policy))

        for label, value in (("first-match", first), ("exact-arch", exact),
                             ("prefer-PTX", ptxish), ("precedence-aware", aware)):
            if value != driver:
                wrong[label] += 1

        rows.append((description, driver, first, exact, ptxish, aware))

    head = ("container", "driver runs", "first-match", "exact-arch",
            "prefer-PTX", "precedence-aware")
    if args.markdown:
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join(["---"] * len(head)) + "|")
        for desc, driver, first, exact, ptxish, aware in rows:
            cells = [desc, f"**{driver}**"] + [
                v + ("" if v == driver else " (wrong)")
                for v in (first, exact, ptxish, aware)]
            print("| " + " | ".join(cells) + " |")
    else:
        width = max(len(r[0]) for r in rows)
        print(f"{'container':<{width}}  {'driver':<12} {'first':<14} "
              f"{'exactArch':<14} {'preferPTX':<14} {'aware':<12}")
        for desc, driver, first, exact, ptxish, aware in rows:
            mark = lambda v: v + ("" if v == driver else " !")
            print(f"{desc:<{width}}  {driver:<12} {mark(first):<14} "
                  f"{mark(exact):<14} {mark(ptxish):<14} {mark(aware):<12}")

    print()
    print(f"{len(rows)} containers, each one measured on the GPU.")
    print("rows where the reading disagrees with what executed:")
    for label, count in wrong.items():
        print(f"  {label:<18} {count:>2} / {len(rows)}")
    return 1 if wrong["precedence-aware"] else 0


if __name__ == "__main__":
    sys.exit(main())
