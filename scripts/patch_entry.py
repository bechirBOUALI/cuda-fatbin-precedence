#!/usr/bin/env python3
"""Set bits in a fat binary entry's `flags` field, leaving everything else alone.

Used to build the architecture-suffix cases in the divergence matrix. Bits 20
and 21 are the suffix of the entry's target name, `a` and `f`, so setting one
changes which GPU the entry claims to be for. The point of these inputs is that
they are structurally perfect: every magic, size and offset stays correct and
only the named bits change, so no parser has a structural reason to object.

    patch_entry.py <in.fatbin> <out.fatbin> --entry 0 --set-bit 20
"""
import argparse
import struct
import sys

FATBIN_HEADER_MAGIC = 0xBA55ED50
FIXED_ENTRY_HEADER = 0x40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--entry", type=int, default=0)
    ap.add_argument("--set-bit", type=int, action="append", default=[])
    args = ap.parse_args()

    d = bytearray(open(args.infile, "rb").read())
    magic, _ver, hsize, fsize = struct.unpack_from("<IHHQ", d, 0)
    if magic != FATBIN_HEADER_MAGIC:
        sys.exit(f"{args.infile}: not a fat binary")

    pos, idx, end = hsize, 0, hsize + fsize
    while pos + FIXED_ENTRY_HEADER <= end:
        ehs, = struct.unpack_from("<I", d, pos + 4)
        psz, = struct.unpack_from("<Q", d, pos + 8)
        if idx == args.entry:
            flags, = struct.unpack_from("<Q", d, pos + 0x28)
            new = flags
            for b in args.set_bit:
                new |= 1 << b
            struct.pack_into("<Q", d, pos + 0x28, new)
            open(args.outfile, "wb").write(bytes(d))
            print(f"{args.outfile}: entry {idx} flags {flags:#x} -> {new:#x}")
            return 0
        pos += ehs + psz
        idx += 1
    sys.exit(f"{args.infile}: no entry {args.entry}")


if __name__ == "__main__":
    sys.exit(main())
