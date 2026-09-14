#!/usr/bin/env python3
"""Make an entry's declared architecture disagree with its payload's own.

A cubin entry states its architecture twice: once in the fat binary entry
header at +0x1c, and once inside the embedded ELF, in `e_flags`, whose third
byte carries the SM number (sm_89 is 0x6005904). Nothing forces the two to
agree. This sets either one independently, so "which field does the driver
select on, and which does a tool read" can be answered by execution.

Only architecture fields change. No size, offset or magic is touched, so the
container stays structurally valid either way.

    patch_arch.py in.fatbin out.fatbin [--entry 0] [--header-arch 75] [--elf-arch 75]
"""
import argparse
import struct
import sys

FATBIN_HEADER_MAGIC = 0xBA55ED50
FIXED_ENTRY_HEADER = 0x40
E_FLAGS_OFF = 0x30          # e_flags within an ELF64 header


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--entry", type=int, default=0)
    ap.add_argument("--header-arch", type=int,
                    help="value for the entry header's arch field at +0x1c")
    ap.add_argument("--elf-arch", type=int,
                    help="SM number to write into the embedded ELF's e_flags")
    args = ap.parse_args()

    d = bytearray(open(args.infile, "rb").read())
    magic, _v, hsize, fsize = struct.unpack_from("<IHHQ", d, 0)
    if magic != FATBIN_HEADER_MAGIC:
        sys.exit(f"{args.infile}: not a fat binary")

    pos, idx = hsize, 0
    while pos + FIXED_ENTRY_HEADER <= hsize + fsize:
        ehs, = struct.unpack_from("<I", d, pos + 4)
        psz, = struct.unpack_from("<Q", d, pos + 8)
        if idx == args.entry:
            if args.header_arch is not None:
                was, = struct.unpack_from("<I", d, pos + 0x1c)
                struct.pack_into("<I", d, pos + 0x1c, args.header_arch)
                print(f"  entry {idx}: header arch {was} -> {args.header_arch}")
            if args.elf_arch is not None:
                pay = pos + ehs
                if bytes(d[pay:pay + 4]) != b"\x7fELF":
                    sys.exit(f"entry {idx} payload is not a raw ELF")
                flags, = struct.unpack_from("<I", d, pay + E_FLAGS_OFF)
                new = (flags & ~0x0000FF00) | (args.elf_arch << 8)
                struct.pack_into("<I", d, pay + E_FLAGS_OFF, new)
                print(f"  entry {idx}: ELF e_flags {flags:#x} -> {new:#x} "
                      f"(sm_{(flags >> 8) & 0xff} -> sm_{args.elf_arch})")
            open(args.outfile, "wb").write(bytes(d))
            return 0
        pos += ehs + psz
        idx += 1
    sys.exit(f"{args.infile}: no entry {args.entry}")


if __name__ == "__main__":
    sys.exit(main())
