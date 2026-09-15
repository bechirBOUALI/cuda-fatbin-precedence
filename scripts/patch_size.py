#!/usr/bin/env python3
"""Edit the size fields of a fat binary, leaving every payload byte alone.

The container states two sizes and neither is what a reader would assume.
`fat_size` bounds the walk, but the driver truncates it to a signed 32-bit
value and only requires an entry to START inside it. `payload_size` advances
the walk, but it does not bound what the driver reads: PTX is read to the first
NUL and an ELF is read as far as its own headers reach. So the same declared
bytes can run different code, and code outside the declared bytes can run.

This script builds the containers that show that, by changing only the fields
in question. No payload byte is touched, and every magic, offset and
architecture field stays correct.

    patch_size.py in.fatbin out.fatbin --entry 0 --payload-size 200
    patch_size.py in.fatbin out.fatbin --fat-size 3177
    patch_size.py in.fatbin out.fatbin --append-entry other.fatbin --fat-size +1
"""
import argparse
import struct
import sys

FATBIN_HEADER_MAGIC = 0xBA55ED50
FIXED_ENTRY_HEADER = 0x40


def entries(d):
    """Yield (index, offset, header_size, payload_size) for each entry."""
    magic, _ver, hsize, fsize = struct.unpack_from("<IHHQ", d, 0)
    if magic != FATBIN_HEADER_MAGIC:
        sys.exit("not a fat binary")
    pos, idx = hsize, 0
    while pos + FIXED_ENTRY_HEADER <= min(hsize + fsize, len(d)):
        ehs, = struct.unpack_from("<I", d, pos + 4)
        psz, = struct.unpack_from("<Q", d, pos + 8)
        if ehs < FIXED_ENTRY_HEADER:
            break
        yield idx, pos, ehs, psz
        if ehs + psz == 0:
            break
        pos += ehs + psz
        idx += 1


def resolve(value, current):
    """Accept an absolute size, or +N / -N relative to the current one."""
    if value.startswith(("+", "-")):
        return current + int(value, 10)
    return int(value, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--entry", type=int, default=0,
                    help="which entry --payload-size applies to")
    ap.add_argument("--payload-size",
                    help="new declared payload size, absolute or +N / -N")
    ap.add_argument("--fat-size",
                    help="new declared container size, absolute or +N / -N")
    ap.add_argument("--append-entry", metavar="FATBIN",
                    help="append entry 0 of FATBIN, header and payload, after "
                         "the end of this container without counting it in "
                         "fat_size unless --fat-size says otherwise")
    ap.add_argument("--append-raw", metavar="FILE",
                    help="append the raw bytes of FILE after the last entry's "
                         "payload, so they sit inside the entry when "
                         "--payload-size is widened to cover them")
    args = ap.parse_args()

    d = bytearray(open(args.infile, "rb").read())
    _magic, _ver, hsize, fsize = struct.unpack_from("<IHHQ", d, 0)

    if args.append_entry:
        src = bytearray(open(args.append_entry, "rb").read())
        _i, off, ehs, psz = next(iter(entries(src)))
        d += src[off:off + ehs + psz]
        print(f"{args.outfile}: appended {ehs + psz} bytes from "
              f"{args.append_entry} at offset {len(d) - ehs - psz:#x}")

    if args.append_raw:
        extra = open(args.append_raw, "rb").read()
        d += extra
        print(f"{args.outfile}: appended {len(extra)} raw bytes from "
              f"{args.append_raw}")

    if args.payload_size is not None:
        for idx, pos, _ehs, psz in entries(d):
            if idx == args.entry:
                new = resolve(args.payload_size, psz)
                struct.pack_into("<Q", d, pos + 8, new)
                print(f"{args.outfile}: entry {idx} payload_size "
                      f"{psz} -> {new}")
                break
        else:
            sys.exit(f"no entry {args.entry}")

    if args.fat_size is not None:
        new = resolve(args.fat_size, fsize)
        struct.pack_into("<Q", d, 8, new & 0xFFFFFFFFFFFFFFFF)
        as_int32 = struct.unpack("<i", struct.pack("<I", new & 0xFFFFFFFF))[0]
        print(f"{args.outfile}: fat_size {fsize} -> {new} "
              f"(the driver walks it as {as_int32})")

    open(args.outfile, "wb").write(bytes(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
