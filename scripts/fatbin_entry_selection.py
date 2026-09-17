#!/usr/bin/env python3
"""Precedence-aware CUDA fat binary parser.

Reads a fat binary, or a host ELF that embeds one, and reports for each entry
what it is and, crucially, WHETHER THE DRIVER WOULD EXECUTE IT.

That last column is the point of this tool. A fat binary can hold several
entries that match the running GPU, and the driver picks exactly one. NVIDIA
documents only the coarse part of how it chooses, that a compatible cubin beats
PTX, and leaves the ranking among several matching candidates unstated. The
full rule is not "the first matching entry", so a tool that inspects, hashes or
attests the wrong entry is describing code the hardware never runs. The rule implemented in `would_execute` was measured black-box and
then confirmed against the driver's own code; see
`analysis/entry-precedence.md` and `analysis/driver-selection-logic.md`.

Usage:
    fatbin_entry_selection.py <file> [--sm 89] [--policy default|force-ptx-jit] [--json]

`<file>` may be a raw .fatbin, or any ELF (object, shared library, executable)
carrying a .nvFatBinSegment section, in which case every container found is
reported.
"""

import argparse
import ctypes
import hashlib
import json
import re
import struct
import sys

# Names and values below are NVIDIA's own, from the toolkit header
#   /usr/local/cuda-<ver>/include/fatbinary_section.h
FATBINC_MAGIC        = 0x466243B1   # 'FbC\xb1', found in .nvFatBinSegment
FATBINC_VERSION      = 1            # filename_or_fatbins is an offline filename
FATBINC_LINK_VERSION = 2            # filename_or_fatbins is an array of prelinked fatbins
FATBIN_HEADER_MAGIC  = 0xBA55ED50   # found in .nv_fatbin

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Entry kinds, identified by construction with libnvfatbin; see
# analysis/fatbin-entry-kinds.md. Values with no name here were seen in the
# driver's own code but could not be produced, so they stay unnamed rather
# than guessed at.
KIND_PTX       = 0x01
KIND_ELF       = 0x02
KIND_CUBIN     = 0x04
KIND_LTO_IR    = 0x08
KIND_MERCURY   = 0x10
KIND_INDEX     = 0x20
KIND_RELOC_PTX = 0x40
KIND_TILEIR    = 0x80
KIND_CONCAT    = 0x100

# Names as cuobjdump itself prints them, read out of its kind-to-name switch:
# the comparisons live at 0x2a895 onward in the CUDA 13.2 build, and 0x100's
# label carries NVIDIA's own spelling. cuobjdump has no case for 0x10 and
# prints "<unknown kind>" for it, so the name used here is descriptive rather
# than NVIDIA's; see analysis/fatbin-entry-kinds.md.
KIND_NAMES = {
    KIND_PTX:       "PTX",
    KIND_ELF:       "ELF",
    KIND_CUBIN:     "CUBIN",
    KIND_LTO_IR:    "NVVM",
    KIND_MERCURY:   "MERCURY?",
    KIND_INDEX:     "INDEX",
    KIND_RELOC_PTX: "RELOC_PTX",
    KIND_TILEIR:    "TILEIR",
    KIND_CONCAT:    "CONCAT",
}

# The driver ranks candidate entries by kind before it considers anything else.
# Order read out of the ranker, which tests each kind on both sides before
# anything else is considered, at 0x474fcc and 0x474fde for PTX and 0x474feb
# and 0x474ff6 for kind 0x80: ELF beats 0x10 beats PTX beats the rest. Anything
# absent here is unranked and never wins against a ranked entry. The full
# cascade is in analysis/decompiled-selection.md.
KIND_RANK = {KIND_ELF: 3, 0x10: 2, KIND_PTX: 1}

# Entry `flags` bits. 0x8000 marks a zstd-compressed payload.
#
# Bits 20 and 21 encode the architecture-name SUFFIX. The driver renders an
# entry's target as sm_<arch><suffix> with snprintf, taking the suffix from
# these two bits: "a" for bit 20, "f" for bit 21, empty when neither is set.
# Those are the same suffixes nvcc exposes as sm_90a and sm_100f, confirmed by
# building for each target and reading the bits back. So an entry is not
# excluded by a flag; it declares a different target than its `arch` field
# alone suggests, and an entry declaring a target the GPU does not report is
# simply not a candidate.
# The flags field carries a compression family, not one bit. Stealthium's
# published BinInfo enum names bits 12 to 15 ZLIBCompression, LZ4Compression,
# LZ4Compression2 and ZSTDCompression; a default CUDA 13.2 build emits zstd,
# and one PTX entry in the shipped libcufile is LZ4.
FLAG_ZLIB           = 1 << 12
FLAG_LZ4            = 1 << 13
FLAG_LZ4_2          = 1 << 14
FLAG_COMPRESSED     = 1 << 15
# Bit 16 marks a payload obfuscated by fatbinary's keyed -reorder-obfuscation.
# The entry keeps its kind, its metadata stays readable, and the code does not:
# without the key neither cuobjdump nor the driver can decode it. See
# analysis/ptx-obfuscation.md.
FLAG_OBFUSCATED     = 1 << 16
FLAG_ARCH_SUFFIX_A  = 1 << 20
FLAG_ARCH_SUFFIX_F  = 1 << 21
# Bit 24 loses the ELF-against-ELF tie-break: where two cubins are otherwise
# equal, the one WITHOUT this bit executes. Read out of the ranker's tail and
# then measured, by setting it on the first of two same-architecture entries
# and watching the second one run instead. It is set by the toolkit on targets
# of compute capability 100 and above.
FLAG_DEPRIORITISE   = 1 << 24


class FatBinCWrapper(ctypes.LittleEndianStructure):
    """NVIDIA's __fatBinC_Wrapper_t: the registration record handed to the
    CUDA runtime at startup. One per fatbin, 24 bytes.

    `data` is a VIRTUAL ADDRESS into .nv_fatbin, not a file offset.

    `filename_or_fatbins` depends on `version`: an offline filename when
    version == FATBINC_VERSION, an array of prelinked fatbins when
    version == FATBINC_LINK_VERSION (i.e. the record the device linker adds).
    """

    _pack_ = 1
    _fields_ = [
        ("magic",               ctypes.c_uint32),
        ("version",             ctypes.c_uint32),
        ("data",                ctypes.c_uint64),
        ("filename_or_fatbins", ctypes.c_uint64),
    ]

    def is_valid(self) -> bool:
        return self.magic == FATBINC_MAGIC

    def is_link_record(self) -> bool:
        return self.version == FATBINC_LINK_VERSION

    def __str__(self) -> str:
        kind = "link" if self.is_link_record() else "offline"
        return (f"__fatBinC_Wrapper_t(version={self.version} [{kind}], "
                f"data=0x{self.data:x}, "
                f"filename_or_fatbins=0x{self.filename_or_fatbins:x})")


class FatBinHeader(ctypes.LittleEndianStructure):
    """Container header, 16 bytes, at the start of every fat binary.

    `fatbin_size` counts the entries only, so the container occupies
    header_size + fatbin_size bytes. Walking by that sum is the only reliable way
    to find container boundaries: scanning for the magic with a text tool gives
    wrong answers, because binary data puts many magics on one "line".
    """

    _pack_ = 1
    _fields_ = [
        ("magic",       ctypes.c_uint32),
        ("version",     ctypes.c_uint16),
        ("header_size", ctypes.c_uint16),
        ("fatbin_size",  ctypes.c_uint64),   # 0x08, the walk bound
    ]

    def is_valid(self) -> bool:
        return self.magic == FATBIN_HEADER_MAGIC


class FatBinEntryHeader(ctypes.LittleEndianStructure):
    """Per-entry header. The fixed part is 0x40 bytes; `header_size` is larger
    when the optional identifier or ptxas-options strings are present, and the
    payload begins at entry + header_size.

    Every field below was confirmed by construction rather than taken from the
    reverse engineering alone:

      kind                  built one fatbin per libnvfatbin Add* function
      padded_payload_size          matches the input file length
      payload_size       matches the zstd stream length
      ptxas_options_offset      moves with identifier length (see read_strings)
      code_version_minor/major   PTX entry reads 9.2, and the decompressed payload
                            begins ".version 9.2"
      arch                  89 for sm_89
      identifier_offset/length   built with --ident=IDENTMARKER99, length 13
      flags                 bit 0x8000 tracks --compress
      obfuscation_key       carries the value of fatbinary --okey, so it is
                            not a reserved field despite reading zero in
                            every ordinary build
      uncompressed_payload     equals the byte count zstd actually produced
    """

    _pack_ = 1
    _fields_ = [
        # Field names follow Stealthium's published struct, so that this code
        # and their write-up can be read side by side. Two of the fields that
        # write-up marks undocumented are named here for what they were
        # measured to hold: identifier_length is their field_24, and
        # obfuscation_key is their field_30.
        ("kind",                 ctypes.c_uint16),   # 0x00
        ("version",              ctypes.c_uint16),   # 0x02, 0x0101 throughout
        ("header_size",          ctypes.c_uint32),   # 0x04
        ("padded_payload_size",  ctypes.c_uint64),   # 0x08, advances the walk
        ("payload_size",         ctypes.c_uint32),   # 0x10, stream length, 0 raw
        ("ptxas_options_offset", ctypes.c_uint32),   # 0x14, see read_strings
        ("code_version_minor",   ctypes.c_uint16),   # 0x18
        ("code_version_major",   ctypes.c_uint16),   # 0x1a
        ("arch",                 ctypes.c_uint32),   # 0x1c, 89 == sm_89
        ("identifier_offset",    ctypes.c_uint32),   # 0x20, from entry start
        ("identifier_length",    ctypes.c_uint32),   # 0x24, their field_24
        ("bin_info",             ctypes.c_uint64),   # 0x28, flags
        ("obfuscation_key",      ctypes.c_uint64),   # 0x30, their field_30
        ("uncompressed_payload", ctypes.c_uint64),   # 0x38
    ]


FIXED_ENTRY_HEADER = 0x40
assert ctypes.sizeof(FatBinEntryHeader) == FIXED_ENTRY_HEADER


def elf_extent(blob, start, avail):
    """How far into the buffer a 64-bit ELF's own headers reach, or None.

    Used to find the bytes the driver reads beyond an entry's declared payload
    size. Every offset is bounds-checked, since these fields are exactly what a
    crafted container controls.
    """
    if avail < 0x40 or bytes(blob[start:start + 4]) != b"\x7fELF":
        return None
    try:
        (e_phoff, e_shoff) = struct.unpack_from("<QQ", blob, start + 0x20)
        (e_phentsize, e_phnum, e_shentsize, e_shnum) = struct.unpack_from(
            "<HHHH", blob, start + 0x36)
    except struct.error:
        return None
    reach = 0x40
    reach = max(reach, e_phoff + e_phentsize * e_phnum)
    reach = max(reach, e_shoff + e_shentsize * e_shnum)
    for i in range(min(e_shnum, 512)):
        off = start + e_shoff + i * e_shentsize
        if e_shentsize < 0x40 or off + 0x40 > len(blob):
            break
        sh_type, = struct.unpack_from("<I", blob, off + 4)
        sh_offset, sh_size = struct.unpack_from("<QQ", blob, off + 0x18)
        if sh_type != 8:                      # SHT_NOBITS occupies no bytes
            reach = max(reach, sh_offset + sh_size)
    return reach if 0 < reach <= avail else None


class Entry:
    """One fat binary entry, with its payload resolved and hashed."""

    def __init__(self, index, offset, hdr, blob):
        self.index = index
        self.offset = offset          # absolute offset of the entry header
        self.hdr = hdr
        self.kind = hdr.kind
        self.arch = hdr.arch
        self.bin_info = hdr.bin_info
        self.kind_name = KIND_NAMES.get(hdr.kind, f"unknown_{hdr.kind:#x}")
        self.compressed = bool(hdr.bin_info & FLAG_COMPRESSED)
        self.lz4 = bool(hdr.bin_info & (FLAG_LZ4 | FLAG_LZ4_2))
        self.zlib = bool(hdr.bin_info & FLAG_ZLIB)
        # payload_size is set for every scheme, so it, rather than the zstd
        # bit alone, is what says the stored bytes are not the device code.
        self.stored_compressed = bool(
            hdr.bin_info & (FLAG_COMPRESSED | FLAG_LZ4 | FLAG_LZ4_2 | FLAG_ZLIB)
            or hdr.payload_size)
        self.obfuscated = bool(hdr.bin_info & FLAG_OBFUSCATED)
        # The key is stored as BCD: the decimal digits of the value supplied to
        # fatbinary --okey, read as hex nibbles. 12345 is stored as 0x12345.
        self.obfuscation_key = (f"{hdr.obfuscation_key:x}"
                                if hdr.obfuscation_key else None)
        self.arch_suffix = ("a" if hdr.bin_info & FLAG_ARCH_SUFFIX_A
                            else "f" if hdr.bin_info & FLAG_ARCH_SUFFIX_F
                            else "")
        self.deprioritised = bool(hdr.bin_info & FLAG_DEPRIORITISE)
        self.notes = []

        self.ident, self.ptxas_options = self.read_strings(blob)
        if self.obfuscated:
            self.notes.append(
                "payload is obfuscated (flags bit 16): the code cannot be read "
                "without the obfuscation key, though the metadata above is "
                "accurate. This is not an entry with no code")

        start = offset + hdr.header_size
        # Two size fields describe the stored bytes and they disagree under
        # attack. The u64 at 0x08 is the padded size, which advances the walk;
        # the u32 at 0x10 is the compressed stream's real length and is 0 when
        # the payload is stored raw. Slicing by the padded field alone loses a
        # compressed payload whose padded size is understated, and the driver
        # still runs it, so take the larger of the two.
        n_stored = max(hdr.padded_payload_size, hdr.payload_size)
        stored = bytes(blob[start:start + n_stored])
        if hdr.payload_size > hdr.padded_payload_size:
            self.notes.append(
                f"the compressed stream is {hdr.payload_size} bytes while "
                f"the padded payload size declares {hdr.padded_payload_size}: the "
                f"driver reads the stream, so a reader bounded by the padded "
                f"size sees less code than runs, or none")
        if len(stored) < n_stored:
            self.notes.append(
                f"payload truncated: header declares {n_stored} bytes, "
                f"{len(stored)} present")
        self.stored_payload = stored
        self.declared_payload = self.decompress(stored)
        self.declared_sha256 = (hashlib.sha256(self.declared_payload).hexdigest()
                                if self.declared_payload else None)

        # padded_payload_size is a STRIDE field, not a content length, and the driver
        # does not use it to bound what it reads. Resolve the extent the driver
        # actually consumes, per kind, and hash that. See resolve_extent.
        self.payload = self.resolve_extent(blob, start)

        # Hash the DECOMPRESSED payload. Hashing the stored bytes would make the
        # same device code hash differently depending only on compression, which
        # is exactly the kind of aliasing an attester must not have.
        self.payload_sha256 = hashlib.sha256(self.payload).hexdigest() if self.payload else None
        self.stride = hdr.header_size + hdr.padded_payload_size
        self.elf_arch = self.read_elf_arch()
        if self.elf_arch is not None and self.elf_arch != self.arch:
            self.notes.append(
                f"architecture disagreement: entry header says {self.arch}, "
                f"embedded ELF e_flags says {self.elf_arch}. The driver selects "
                f"on the header; cuobjdump -lelf reports the ELF value")

    def resolve_extent(self, blob, start):
        """The bytes the driver actually reads for this entry.

        `padded_payload_size` advances the walk. It does not bound the read, and the
        two differ in both directions, which is what breaks a tool that hashes
        `payload[0 : padded_payload_size]`:

          PTX   the payload is read as a NUL-terminated string. An entry
                declaring zero bytes still compiles and runs a full kernel, and
                two containers whose declared bytes are byte-identical can run
                different code.
          ELF   the embedded ELF's own headers decide what is read, so bytes
                past the declared payload still change the outcome: the same
                declared bytes load or fail depending on a tail the declared
                size excludes.

        Both directions are recorded as notes, because a hash taken over the
        wrong extent is wrong silently.
        """
        if self.obfuscated:
            return self.declared_payload

        declared = self.hdr.padded_payload_size

        if self.kind in (KIND_PTX, KIND_RELOC_PTX):
            if self.stored_compressed:
                text = self.declared_payload
                cut = text.find(b"\x00")
                return text if cut < 0 else text[:cut]
            cut = blob.find(b"\x00", start, len(blob))
            end = len(blob) if cut < 0 else cut
            if end - start > declared:
                self.notes.append(
                    f"PTX text runs {end - start - declared} bytes past the "
                    f"declared padded_payload_size: the driver reads to the first NUL, "
                    f"so hashing the declared bytes hashes neither all nor only "
                    f"the code that runs")
            elif end - start < declared:
                self.notes.append(
                    f"declared padded_payload_size covers {declared - (end - start)} "
                    f"bytes past the PTX terminator, which the driver never "
                    f"reads")
            return bytes(blob[start:end])

        if self.kind == KIND_ELF and not self.stored_compressed:
            reach = elf_extent(blob, start, len(blob) - start)
            if reach is not None and reach != declared:
                if reach > declared:
                    self.notes.append(
                        f"the embedded ELF describes {reach - declared} bytes "
                        f"past the declared padded_payload_size, and the driver reads "
                        f"them: bytes outside the declared payload decide "
                        f"whether this entry loads")
                else:
                    self.notes.append(
                        f"the declared padded_payload_size covers {declared - reach} "
                        f"bytes past the end of the embedded ELF, which the "
                        f"driver never reads. Anything in that gap, a whole "
                        f"second cubin included, is carried by the container "
                        f"and executed by nothing")
                return bytes(blob[start:start + min(reach, len(blob) - start)])
        return self.declared_payload

    def read_elf_arch(self):
        """The SM number the embedded ELF claims for itself, or None.

        A cubin entry states its architecture twice and nothing makes the two
        agree. The driver selects on the entry header and only afterwards
        validates the ELF, so the fields can differ without the container
        failing, as long as the ELF is still loadable on the GPU that was
        selected for. cuobjdump's entry listing reports this value while its
        ELF dump reports the header value, so the two disagree on the same
        entry and a tool reading the listing is reading the field selection
        does not use.

        Where the SM number sits inside e_flags depends on the cubin ELF ABI
        version, byte 8 of e_ident, and getting this wrong invents
        disagreements that are not there:

            abiver 7 (osabi 51)   arch in bits 16..23   sm_75 is 0x004b054b
            abiver 8 (osabi 65)   arch in bits  8..15   sm_89 is 0x06005904

        Both layouts occur in ordinary files: the CUDA 13.2 toolkit emits
        abiver 8, while cubins inside its own shipped libraries are abiver 7.
        An unrecognised version returns None and is not checked, because a
        false disagreement is worse than a missed one.
        """
        if len(self.payload) < 0x34 or not self.payload.startswith(b"\x7fELF"):
            return None
        abiver = self.payload[8]
        shift = {7: 16, 8: 8}.get(abiver)
        if shift is None:
            return None
        flags, = struct.unpack_from("<I", self.payload, 0x30)
        return (flags >> shift) & 0xFF

    def read_strings(self, blob):
        """Recover the identifier and the ptxas options string.

        The identifier is a plain (offset, length) pair at 0x20/0x24. The
        options string is one level of indirection deeper: u32 at 0x14 gives
        the offset of a descriptor, and that descriptor is itself an
        (offset, length) pair. That layout was established by building entries
        with identifiers of different lengths and watching the descriptor
        offset move to just past the identifier, 8-byte aligned.

        Both are bounds-checked against header_size before use. Nothing in the
        container guarantees these offsets point inside it, so a parser that
        trusts them can be walked off the end of its own buffer by a crafted
        file. Out-of-bounds values are recorded as a note on the entry rather
        than raising, because a scanner has to keep going and report what it
        saw.
        """
        h = self.hdr
        base = self.offset
        # header_size is attacker-controlled and need not fit in the buffer, so
        # clamp it before it is used as a bound. Trusting it crashes the parser
        # on a crafted file, which is the failure this function warns about.
        limit = min(h.header_size, max(0, len(blob) - base))
        if limit < h.header_size:
            self.notes.append(
                f"header_size {h.header_size} extends past the end of the "
                f"buffer; strings bounded to {limit} bytes instead")

        def grab(off, length, what):
            if length == 0:
                return ""
            if off + length > limit:
                self.notes.append(
                    f"{what} out of bounds: offset {off} length {length} "
                    f"exceeds header_size {limit}")
                return ""
            raw = bytes(blob[base + off:base + off + length])
            return raw.split(b"\x00")[0].decode("utf-8", "replace")

        ident = grab(h.identifier_offset, h.identifier_length, "identifier")

        options = ""
        # The descriptor lives in the variable part of the header, past the
        # fixed 64 bytes. A zero offset means there is no descriptor at all, and
        # reading one anyway lands on the entry's own kind and version fields
        # and reports them as a malformed string. A stock
        # `fatbinary --ident=...` container has exactly that shape.
        if (h.header_size > FIXED_ENTRY_HEADER
                and FIXED_ENTRY_HEADER <= h.ptxas_options_offset
                and h.ptxas_options_offset + 8 <= limit
                and base + h.ptxas_options_offset + 8 <= len(blob)):
            oo, ol = struct.unpack_from(
                "<II", blob, base + h.ptxas_options_offset)
            options = grab(oo, ol, "ptxas options")
        return ident, options

    def decompress(self, stored):
        """Return the entry's device code, decompressing when needed.

        Compression is indicated by flag 0x8000, NOT by the entry kind. A
        default build happens to compress PTX and store cubins raw, which makes
        it tempting to key off the kind, but `fatbinary --compress-all` produces
        compressed ELF entries and such a parser reads them as garbage.
        """
        if not stored:
            return b""
        if self.obfuscated:
            # Deliberately not attempted. The stored bytes are not a valid zstd
            # frame, and reporting a decompression failure here would describe
            # it as corrupt when it is intact and merely unreadable.
            return b""
        if self.lz4 or (self.hdr.payload_size and not self.compressed
                        and not stored.startswith(ZSTD_MAGIC) and not self.zlib):
            return self.decompress_lz4(stored)
        if self.zlib and not self.compressed:
            self.notes.append(
                "payload is zlib-compressed, flags bit 12, which this parser "
                "does not decode: no sample was available to test against")
            return b""
        if not self.compressed and not stored.startswith(ZSTD_MAGIC):
            return stored
        try:
            import zstandard
        except ImportError:
            self.notes.append("payload is compressed and the zstandard module is missing")
            return b""

        # payload_size is the real stream length; padded_payload_size is padded, and
        # feeding the padding to the decompressor is what makes naive readers
        # report a corrupt frame on a perfectly good entry.
        n = self.hdr.payload_size or len(stored)
        try:
            out = zstandard.ZstdDecompressor().decompress(
                stored[:n], max_output_size=max(self.hdr.uncompressed_payload, 1 << 26))
        except Exception as exc:
            self.notes.append(f"decompression failed: {exc}")
            return b""
        if self.hdr.uncompressed_payload and len(out) != self.hdr.uncompressed_payload:
            self.notes.append(
                f"decompressed size mismatch: header says "
                f"{self.hdr.uncompressed_payload}, got {len(out)}")
        return out

    def decompress_lz4(self, stored):
        """Decompress an LZ4 entry, the way the driver's consumers do.

        The payload is a raw LZ4 block, not a frame, so the decompressed size
        has to come from the header rather than from the stream. ZLUDA does the
        same thing through LZ4_decompress_safe, growing the output buffer when
        the hint is short, so the hint is treated as a hint here too.
        """
        try:
            import lz4.block
        except ImportError:
            self.notes.append(
                "payload is LZ4-compressed and the lz4 module is missing")
            return b""

        n = self.hdr.payload_size or len(stored)
        hint = max(1024, self.hdr.uncompressed_payload)
        for _ in range(8):
            try:
                out = lz4.block.decompress(stored[:n], uncompressed_size=hint)
            except Exception:
                hint *= 2
                continue
            if self.hdr.uncompressed_payload and len(out) != self.hdr.uncompressed_payload:
                self.notes.append(
                    f"decompressed size mismatch: header says "
                    f"{self.hdr.uncompressed_payload}, got {len(out)}")
            return out
        self.notes.append("LZ4 decompression failed")
        return b""

    def arch_label(self):
        """The target this entry declares, suffix included.

        The suffix is the part a listing tends to drop: cuobjdump's own entry
        listing prints sm_89 for an entry whose flags say sm_89a, while its ELF
        dump prints sm_89a. Anything matching on the listing is therefore
        matching on a name the driver does not use.
        """
        # The driver bounds the architecture to 1..999 before rendering the
        # name, cmp $0x3e6 at 0x474b59, so a value outside that range is not a
        # target at all and printing sm_<huge> would invent one.
        if not 1 <= self.arch <= 999:
            return f"invalid ({self.arch})"
        prefix = "compute_" if self.kind == KIND_PTX else "sm_"
        return f"{prefix}{self.arch}{self.arch_suffix}"


class Container:
    """A container header, plus the notes its walk produced.

    Stands in for the raw header struct so that findings about the container
    itself, rather than about one entry, have somewhere to live.
    """

    def __init__(self, hdr, offset):
        self.version = hdr.version
        self.header_size = hdr.header_size
        self.fatbin_size = hdr.fatbin_size
        self.offset = offset
        self.notes = []
        # The driver loads fatbin_size as a u64 and then truncates it to a signed
        # 32-bit value before using it as the walk bound (`movslq %esi,%rax`).
        # Reading it as a u64 disagrees with the driver by up to 2**64 - 2**32.
        self.declared = ctypes.c_int32(hdr.fatbin_size & 0xFFFFFFFF).value


def parse_container(blob, offset=0):
    """Parse one container at `offset`. Returns (Container, [Entry]).

    The walk reproduces the driver's bound rather than a reasonable one. Two
    details decide which entries exist at all, and both were measured:

      * the bound is the declared size truncated to a signed 32-bit value, so a
        container declaring 0x800018d0 bytes is walked as a negative size and
        no entry is reached;
      * an entry is walked when its START lies inside that bound. Its header
        and payload may extend past the end of the declared container, and the
        driver reads them anyway, so an entry can execute while a size-honouring
        reader does not see it at all.

    Everything the walk has to clamp for its own safety, rather than because
    the driver does, is recorded as a note instead of passing in silence.
    """
    if len(blob) - offset < 16:
        raise InputError(
            f"only {len(blob) - offset} bytes at offset {offset:#x}, too few "
            f"for the 16-byte container header")
    hdr = FatBinHeader.from_buffer_copy(bytes(blob[offset:offset + 16]))
    if not hdr.is_valid():
        raise ValueError(f"no fat binary magic at offset {offset:#x}")

    container = Container(hdr, offset)
    declared = container.declared
    first = offset + hdr.header_size

    if hdr.fatbin_size >> 32:
        container.notes.append(
            f"fatbin_size declares {hdr.fatbin_size} bytes, but the driver truncates "
            f"it to a signed 32-bit value, {declared}. The upper bits are "
            f"discarded")
    if hdr.fatbin_size and declared <= 0:
        container.notes.append(
            f"fatbin_size truncates to {declared}, which is not positive: the "
            f"driver walks no entries and the container cannot load, however "
            f"many entries a u64-reading parser finds")
    if declared > 0 and first + declared > len(blob):
        container.notes.append(
            f"the declared container ends {first + declared - len(blob)} bytes "
            f"past the end of this buffer. cuModuleLoadData takes a pointer "
            f"with no length, so the driver reads whatever follows the caller's "
            f"memory")

    entries = []
    pos = first
    while pos - first < declared:
        if pos + FIXED_ENTRY_HEADER > len(blob):
            container.notes.append(
                f"entry {len(entries)} starts inside the declared container but "
                f"its header runs past the end of this buffer")
            break
        eh = FatBinEntryHeader.from_buffer_copy(
            bytes(blob[pos:pos + FIXED_ENTRY_HEADER]))
        if eh.header_size < FIXED_ENTRY_HEADER:
            break

        # An entry whose declared payload does not cover the bytes the driver
        # actually reads leaves the walk pointing into the middle of that
        # payload, where the next "entry header" is really code or text. The
        # driver reads it as a header too, finds a kind it cannot select, and
        # its stride then carries the walk out of the container. Reporting the
        # invented fields of such a header as though they were metadata is
        # worse than useless, so say what is there and stop.
        room = len(blob) - pos
        if eh.kind not in KIND_NAMES or eh.header_size > room:
            reason = (f"kind {eh.kind:#x} is not a fat binary entry kind"
                      if eh.kind not in KIND_NAMES else
                      f"its header_size {eh.header_size} exceeds the {room} "
                      f"bytes left in the buffer")
            blamed = (f", and the walk reached them because entry "
                      f"{len(entries) - 1} declares a payload shorter than the "
                      f"one the driver reads" if entries else "")
            preview = bytes(blob[pos:pos + 16])
            container.notes.append(
                f"the bytes at offset {pos:#x} do not parse as an entry "
                f"header: {reason}{blamed}. They are {preview!r}. The driver "
                f"reads them as a header too, finds nothing it can select, and "
                f"its stride carries the walk past the end of the container")
            break

        entry = Entry(len(entries), pos, eh, blob)
        entries.append(entry)
        if entry.stride == 0:
            entry.notes.append(
                "zero stride: nothing advances the walk past this entry, so "
                "parsing stops here and any later entry is unreported")
            break
        over = (pos + entry.stride) - (first + declared)
        if over > 0:
            entry.notes.append(
                f"this entry extends {over} bytes past the end of the declared "
                f"container. The driver walks it because its header starts "
                f"inside; cuobjdump does not list it unless its whole 64-byte "
                f"header fits")
        pos += entry.stride

    if pos + FIXED_ENTRY_HEADER <= len(blob):
        tail = FatBinEntryHeader.from_buffer_copy(
            bytes(blob[pos:pos + FIXED_ENTRY_HEADER]))
        if tail.kind in KIND_NAMES and tail.header_size >= FIXED_ENTRY_HEADER:
            container.notes.append(
                f"{len(blob) - pos} bytes after the last walked entry parse as "
                f"a further entry header, kind {tail.kind:#x}, outside the "
                f"declared container")

    return container, entries


def walk_nv_fatbin(raw, start, size):
    """Yield the offset of every container in .nv_fatbin, by header chaining.

    Read the magic, then jump by header_size + fatbin_size to reach the next
    container. This is the only reliable enumeration. Searching for the magic
    with a text tool gives wrong answers, because binary data puts many magics
    on one "line" and a byte pattern can occur inside a payload; chaining
    cannot land in the middle of an entry.

    It also preserves container boundaries, which matters because cuobjdump
    flattens every container in a file into one numbered list, so its output
    cannot tell you which entries were competing with each other and which
    merely sit in the same binary.
    """
    pos = start
    end = start + size
    while pos + 16 <= end:
        hdr = FatBinHeader.from_buffer_copy(raw[pos:pos + 16])
        if not hdr.is_valid():
            break
        yield pos
        stride = hdr.header_size + hdr.fatbin_size
        if stride <= 0:
            break
        pos += stride


class InputError(ValueError):
    """The file is not something this tool can read, with the reason why.

    Raised instead of failing on a traceback, because the common mistakes,
    handing it a bare cubin or a host binary with no device code, are ordinary
    and deserve an answer rather than a stack trace. It subclasses ValueError
    so that callers sweeping a directory, survey_libs.py among them, keep
    skipping unreadable files the way they always did.
    """


def find_containers(path):
    """Yield (description, offset, blob) for every fat binary in `path`.

    Handles a raw .fatbin directly. In an ELF host binary the containers live in
    .nv_fatbin and are registered by wrapper records in .nvFatBinSegment, whose
    `data` field is a VIRTUAL ADDRESS, so reaching them needs the section whose
    sh_addr range covers it:
        file_offset = sh_offset + (data - sh_addr)

    That works for executables and shared libraries. It cannot work for a
    relocatable object, where sh_addr is zero everywhere and the wrapper's
    pointer is supplied by a relocation at link time, so the wrapper reads as a
    null address. For those, and for anything else the wrapper path cannot
    resolve, .nv_fatbin is walked directly instead.
    """
    raw = open(path, "rb").read()

    if len(raw) >= 4 and struct.unpack_from("<I", raw, 0)[0] == FATBIN_HEADER_MAGIC:
        yield f"{path}", 0, raw
        return

    if not raw:
        raise InputError(f"{path} is empty")

    if raw[:4] != b"\x7fELF":
        head = raw[:8].hex(" ")
        raise InputError(
            f"{path} is neither a fat binary container nor an ELF: it starts "
            f"{head}, and a container starts with the magic "
            f"{FATBIN_HEADER_MAGIC:#x}. This tool reads a .fatbin, or an "
            f"object, executable or shared library with one embedded")

    # A bare cubin is an ELF for the CUDA machine type, EM_CUDA 190. It is one
    # device image rather than a container of them, so there is no selection to
    # model and saying so is more use than reporting a missing section.
    if len(raw) >= 20 and struct.unpack_from("<H", raw, 18)[0] == 190:
        raise InputError(
            f"{path} is a bare cubin, a single device image, not a fat binary "
            f"container. There is no entry to select between. Use cuobjdump on "
            f"it directly, or pass the .fatbin that carries it")

    from elftools.elf.elffile import ELFFile
    with open(path, "rb") as f:
        elf = ELFFile(f)
        sections = [(s.header["sh_addr"], s.header["sh_size"],
                     s.header["sh_offset"], s.name)
                    for s in elf.iter_sections()]
        seg = elf.get_section_by_name(".nvFatBinSegment")
        wrappers = bytearray(seg.data()) if seg is not None else bytearray()
        fatbin = elf.get_section_by_name(".nv_fatbin")
        fat_off = fatbin.header["sh_offset"] if fatbin is not None else None
        fatbin_size = fatbin.header["sh_size"] if fatbin is not None else 0

    def va_to_off(va):
        for addr, size, off, _name in sections:
            if addr and addr <= va < addr + size:
                return off + (va - addr)
        return None

    seen = set()
    size = ctypes.sizeof(FatBinCWrapper)
    for woff in range(0, len(wrappers) - size + 1, size):
        w = FatBinCWrapper.from_buffer(wrappers, woff)
        if not w.is_valid():
            continue
        foff = va_to_off(w.data)
        if foff is None:
            continue
        seen.add(foff)
        kind = "link" if w.is_link_record() else "offline"
        yield f"{path} [wrapper +{woff:#x}, {kind}]", foff, raw

    if fat_off is None:
        if not seen:
            raise InputError(
            f"{path} is an ELF with no .nv_fatbin section, so it carries no "
            f"GPU code this tool can read")
        return
    for foff in walk_nv_fatbin(raw, fat_off, fatbin_size):
        if foff not in seen:
            yield f"{path} [.nv_fatbin +{foff - fat_off:#x}]", foff, raw


# ---------------------------------------------------------------------------
# Selection: which entry does the driver actually run
# ---------------------------------------------------------------------------

def arch_compatible(entry, sm, suffix=""):
    """Can this entry run on the target `sm` with suffix `suffix`?

    Three conditions. The entry's architecture must not exceed the GPU's. A
    cubin is binary compatible only inside its major generation, so an sm_86
    cubin runs on sm_89 but an sm_75 cubin does not, while PTX is compiled at
    load time and so is compatible with any later architecture. And the
    architecture-name suffix must match what the GPU reports, because the
    driver matches on the rendered name: an entry whose flags make it sm_89a is
    not a candidate on a GPU reporting sm_89.

    The suffix rule is measured only for the sm_89 case here. Whether a genuine
    sm_90a cubin is a candidate on a cc 9.0 device was not testable on this
    hardware, so callers wanting that case should pass the suffix explicitly
    rather than assume.
    """
    if entry.arch_suffix != suffix:
        return False
    if entry.arch > sm:
        return False
    if entry.kind == KIND_ELF:
        return entry.arch // 10 == sm // 10
    return True


def would_execute(entries, sm, policy="default", suffix=""):
    """Return the entry the driver would select, or None.

    Four ranking levels below the compatibility filter, each consulted only
    when the one above it ties:

      1. kind          ELF beats 0x10 beats PTX. Hard-coded, so no position or
                       architecture advantage overturns it: an sm_86 cubin wins
                       against an exactly matching compute_89 PTX.
      2. architecture  nearest compatible wins, independent of file order.
      3. flag bit 24   among ELF entries still tied, one WITHOUT bit 24 beats
                       one with it. Sits above file order, below architecture.
      4. file order    ELF: the first wins. PTX: the LAST wins. The direction
                       genuinely reverses with the kind, so there is no single
                       positional convention to implement.

    Under CUDA_FORCE_PTX_JIT the driver swaps in a different filter that
    discards ELF entries before ranking, rather than demoting them, so pass
    policy="force-ptx-jit" to model that host.
    """
    pool = [e for e in entries
            if arch_compatible(e, sm, suffix)
            and e.kind in KIND_RANK]
    if policy == "force-ptx-jit":
        pool = [e for e in pool if e.kind != KIND_ELF]
    if not pool:
        return None

    best = None
    for cand in pool:                      # linear scan, incumbent held
        if best is None:
            best = cand
            continue
        rank_b, rank_c = KIND_RANK[best.kind], KIND_RANK[cand.kind]
        if rank_c != rank_b:
            if rank_c > rank_b:
                best = cand
            continue
        if cand.arch != best.arch:
            if cand.arch > best.arch:      # nearer to the GPU, both are <= sm
                best = cand
            continue
        # Kind and architecture tie. For ELF, bit 24 breaks it before position
        # does: the entry lacking the bit wins. Only if that also ties does
        # position decide, and its direction depends on the kind.
        if cand.kind == KIND_ELF and cand.deprioritised != best.deprioritised:
            if best.deprioritised:
                best = cand
            continue
        if cand.kind == KIND_PTX:
            best = cand
    return best


def payload_rejected(entry, sm):
    """Would the driver refuse this entry's payload once it actually reads it?

    Selection and validation are separate steps, and the error codes prove the
    order: an entry whose header claims a selectable architecture but whose
    embedded ELF claims an incompatible one is chosen and then rejected with
    CUDA_ERROR_INVALID_SOURCE, while an entry whose header is unselectable
    never gets that far and yields CUDA_ERROR_NO_BINARY_FOR_GPU.

    There is no fallback. Selection commits to one entry, so a container whose
    chosen entry is rejected fails to load even when a perfectly good entry for
    the same GPU sits directly after it. That is worth stating because it is
    the opposite of what "best matching" suggests.
    """
    if entry is None or entry.kind != KIND_ELF or entry.elf_arch is None:
        return False
    return entry.elf_arch // 10 != sm // 10 or entry.elf_arch > sm


def naive_first_match(entries, sm):
    """What a conventional scanner reports: the first entry that looks like it
    matches the GPU. This is the baseline `would_execute` is compared against,
    and every disagreement between the two is a divergence."""
    for e in entries:
        if e.arch <= sm:
            return e
    return None


def naive_exact_arch(entries, sm):
    """The most careful conventional reading: take the entry whose architecture
    matches the GPU exactly, earliest first, and fall back to first-match.

    This one is worth stating separately because it is RIGHT on ordinary
    shipped libraries, which carry one entry per architecture in ascending
    order. It fails only on inputs that were built to make it fail, which is
    the property you least want in a security scanner: correct on benign
    samples, wrong on adversarial ones.
    """
    for e in entries:
        if e.arch == sm:
            return e
    return naive_first_match(entries, sm)


def naive_prefer_ptx(entries, sm):
    """A second common scanner shape: read the PTX because it is text and needs
    no disassembler. Falls back to the first match when there is no PTX."""
    for e in entries:
        if e.kind == KIND_PTX and e.arch <= sm:
            return e
    return naive_first_match(entries, sm)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def describe(path, sm, policy, suffix=""):
    out = []
    for desc, off, blob in find_containers(path):
        hdr, entries = parse_container(blob, off)
        winner = would_execute(entries, sm, policy, suffix)
        first = naive_first_match(entries, sm)
        exact = naive_exact_arch(entries, sm)
        ptxish = naive_prefer_ptx(entries, sm)
        out.append({
            "source": desc,
            "offset": off,
            "version": hdr.version,
            "header_size": hdr.header_size,
            "fatbin_size": hdr.fatbin_size,
            "container_notes": hdr.notes,
            "policy": policy,
            "sm": sm,
            "target": f"sm_{sm}{suffix}",
            "would_execute": winner.index if winner else None,
            "rejected_at_load": payload_rejected(winner, sm),
            "naive_first_match": first.index if first else None,
            "naive_exact_arch": exact.index if exact else None,
            "naive_prefer_ptx": ptxish.index if ptxish else None,
            "entries": [{
                "index": e.index,
                "offset": e.offset,
                "kind": e.kind,
                "kind_name": e.kind_name,
                "arch": e.arch,
                "arch_label": e.arch_label(),
                "ptx_isa": f"{e.hdr.code_version_major}.{e.hdr.code_version_minor}",
                "header_size": e.hdr.header_size,
                "padded_payload_size": e.hdr.padded_payload_size,
                "compressed": e.compressed,
                "compression": ("obf" if e.obfuscated else "zstd" if e.compressed
                                else "lz4" if e.lz4 else "zlib" if e.zlib
                                else "-"),
                "payload_size": e.hdr.payload_size,
                "uncompressed_payload": e.hdr.uncompressed_payload,
                "bin_info": f"{e.bin_info:#x}",
                "arch_suffix": e.arch_suffix,
                "obfuscated": e.obfuscated,
                "obfuscation_key": e.obfuscation_key,
                "elf_arch": e.elf_arch,
                "deprioritised": e.deprioritised,
                "identifier": e.ident,
                "ptxas_options": e.ptxas_options,
                "payload_sha256": e.payload_sha256,
                "executes": bool(winner and winner.index == e.index),
                "notes": e.notes,
            } for e in entries],
        })
    return out


def print_report(containers):
    for c in containers:
        print(f"== {c['source']}")
        print(f"   container at {c['offset']:#x}, version {c['version']}, "
              f"header {c['header_size']}, fatbin_size {c['fatbin_size']}, "
              f"{len(c['entries'])} entries, target {c['target']}, "
              f"policy {c['policy']}")
        for n in c.get("container_notes", []):
            print(f"   container note: {n}")
        print(f"   {'#':>2}  {'kind':<9} {'arch':<11} {'payload':>8} "
              f"{'comp':<5} {'bin_info':<10} {'sha256':<16} runs")
        for e in c["entries"]:
            print(f"   {e['index']:>2}  {e['kind_name']:<9} {e['arch_label']:<11} "
                  f"{e['padded_payload_size']:>8} "
                  f"{e['compression']:<5} "
                  f"{e['bin_info']:<10} {(e['payload_sha256'] or '')[:16]:<16} "
                  f"{'<== EXECUTES' if e['executes'] else ''}")
            if e["identifier"]:
                print(f"       identifier: {e['identifier']}")
            if e["ptxas_options"]:
                print(f"       ptxas options: {e['ptxas_options']}")
            if e["obfuscation_key"]:
                print(f"       obfuscation key: {e['obfuscation_key']} "
                      f"(stored in the container)")
            for n in e["notes"]:
                print(f"       note: {n}")
        if c["would_execute"] is None:
            print("   no entry is selectable: this container will not load")
        elif c["rejected_at_load"]:
            print("   the selected entry will be refused when its payload is read, "
                  "and the driver does not fall back to another entry")
        naive = c["naive_first_match"]
        if naive is not None and naive != c["would_execute"]:
            print(f"   DIVERGENCE: first-match scanner reports entry {naive}, "
                  f"driver runs entry {c['would_execute']}")
        exact = c["naive_exact_arch"]
        if exact is not None and exact != c["would_execute"]:
            print(f"   DIVERGENCE: exact-arch scanner reports entry {exact}, "
                  f"driver runs entry {c['would_execute']}")
        ptxish = c["naive_prefer_ptx"]
        if ptxish is not None and ptxish != c["would_execute"]:
            print(f"   DIVERGENCE: prefer-PTX scanner reports entry {ptxish}, "
                  f"driver runs entry {c['would_execute']}")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--sm", type=int, default=89,
                    help="compute capability of the target GPU, default 89")
    ap.add_argument("--target",
                    help="target name instead of --sm, e.g. sm_89 or sm_90a; "
                         "the suffix matters, since the driver matches on the "
                         "rendered name")
    ap.add_argument("--policy", choices=("default", "force-ptx-jit"),
                    default="default",
                    help="force-ptx-jit models a host with CUDA_FORCE_PTX_JIT=1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sm, suffix = args.sm, ""
    if args.target:
        m = re.fullmatch(r"(?:sm_|compute_)?(\d+)([af]?)", args.target)
        if not m:
            ap.error(f"cannot parse target {args.target!r}")
        sm, suffix = int(m.group(1)), m.group(2)

    try:
        containers = describe(args.file, sm, args.policy, suffix)
    except InputError as exc:
        sys.exit(f"{exc}")
    except FileNotFoundError:
        sys.exit(f"{args.file}: no such file")
    except IsADirectoryError:
        sys.exit(f"{args.file} is a directory, not a file")
    except PermissionError:
        sys.exit(f"{args.file}: cannot be read")
    if args.json:
        json.dump(containers, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print_report(containers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
