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
`analysis/step1-entry-precedence.md` and `analysis/driver-selection-logic.md`.

Usage:
    fatbin_parser.py <file> [--sm 89] [--policy default|force-ptx-jit] [--json]

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
# Order recovered from the two hard-coded comparisons at 0x474766 and 0x474906:
# ELF beats 0x10 beats PTX beats the rest. Anything absent here is unranked and
# never wins against a ranked entry.
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

    `fat_size` counts the entries only, so the container occupies
    header_size + fat_size bytes. Walking by that sum is the only reliable way
    to find container boundaries: scanning for the magic with a text tool gives
    wrong answers, because binary data puts many magics on one "line".
    """

    _pack_ = 1
    _fields_ = [
        ("magic",       ctypes.c_uint32),
        ("version",     ctypes.c_uint16),
        ("header_size", ctypes.c_uint16),
        ("fat_size",    ctypes.c_uint64),
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
      payload_size          matches the input file length
      compressed_size       matches the zstd stream length
      opts_desc_offset      moves with identifier length (see read_strings)
      version_minor/major   PTX entry reads 9.2, and the decompressed payload
                            begins ".version 9.2"
      arch                  89 for sm_89
      ident_offset/length   built with --ident=IDENTMARKER99, length 13
      flags                 bit 0x8000 tracks --compress
      obfuscation_key       carries the value of fatbinary --okey, so it is
                            not a reserved field despite reading zero in
                            every ordinary build
      decompressed_size     equals the byte count zstd actually produced
    """

    _pack_ = 1
    _fields_ = [
        ("kind",              ctypes.c_uint16),   # 0x00
        ("version",           ctypes.c_uint16),   # 0x02, 0x0101 throughout
        ("header_size",       ctypes.c_uint32),   # 0x04
        ("payload_size",      ctypes.c_uint64),   # 0x08, padded
        ("compressed_size",   ctypes.c_uint32),   # 0x10, 0 when stored raw
        ("opts_desc_offset",  ctypes.c_uint32),   # 0x14, see read_strings
        ("version_minor",     ctypes.c_uint16),   # 0x18
        ("version_major",     ctypes.c_uint16),   # 0x1a
        ("arch",              ctypes.c_uint32),   # 0x1c, 89 == sm_89
        ("ident_offset",      ctypes.c_uint32),   # 0x20, from entry start
        ("ident_length",      ctypes.c_uint32),   # 0x24
        ("flags",             ctypes.c_uint64),   # 0x28
        ("obfuscation_key",   ctypes.c_uint64),   # 0x30, 0 unless a key was set
        ("decompressed_size", ctypes.c_uint64),   # 0x38
    ]


FIXED_ENTRY_HEADER = 0x40
assert ctypes.sizeof(FatBinEntryHeader) == FIXED_ENTRY_HEADER


class Entry:
    """One fat binary entry, with its payload resolved and hashed."""

    def __init__(self, index, offset, hdr, blob):
        self.index = index
        self.offset = offset          # absolute offset of the entry header
        self.hdr = hdr
        self.kind = hdr.kind
        self.arch = hdr.arch
        self.flags = hdr.flags
        self.kind_name = KIND_NAMES.get(hdr.kind, f"unknown_{hdr.kind:#x}")
        self.compressed = bool(hdr.flags & FLAG_COMPRESSED)
        self.obfuscated = bool(hdr.flags & FLAG_OBFUSCATED)
        # The key is stored as BCD: the decimal digits of the value supplied to
        # fatbinary --okey, read as hex nibbles. 12345 is stored as 0x12345.
        self.obfuscation_key = (f"{hdr.obfuscation_key:x}"
                                if hdr.obfuscation_key else None)
        self.arch_suffix = ("a" if hdr.flags & FLAG_ARCH_SUFFIX_A
                            else "f" if hdr.flags & FLAG_ARCH_SUFFIX_F
                            else "")
        self.deprioritised = bool(hdr.flags & FLAG_DEPRIORITISE)
        self.notes = []

        self.ident, self.ptxas_options = self.read_strings(blob)
        if self.obfuscated:
            self.notes.append(
                "payload is obfuscated (flags bit 16): the code cannot be read "
                "without the obfuscation key, though the metadata above is "
                "accurate. This is not an entry with no code")

        start = offset + hdr.header_size
        stored = bytes(blob[start:start + hdr.payload_size])
        if len(stored) < hdr.payload_size:
            self.notes.append(
                f"payload truncated: header declares {hdr.payload_size} bytes, "
                f"{len(stored)} present")
        self.stored_payload = stored
        self.payload = self.decompress(stored)

        # Hash the DECOMPRESSED payload. Hashing the stored bytes would make the
        # same device code hash differently depending only on compression, which
        # is exactly the kind of aliasing an attester must not have.
        self.payload_sha256 = hashlib.sha256(self.payload).hexdigest() if self.payload else None
        self.stride = hdr.header_size + hdr.payload_size
        self.elf_arch = self.read_elf_arch()
        if self.elf_arch is not None and self.elf_arch != self.arch:
            self.notes.append(
                f"architecture disagreement: entry header says {self.arch}, "
                f"embedded ELF e_flags says {self.elf_arch}. The driver selects "
                f"on the header; cuobjdump -lelf reports the ELF value")

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
        limit = h.header_size

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

        ident = grab(h.ident_offset, h.ident_length, "identifier")

        options = ""
        if h.header_size > FIXED_ENTRY_HEADER and h.opts_desc_offset + 8 <= limit:
            oo, ol = struct.unpack_from(
                "<II", blob, base + h.opts_desc_offset)
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
        if not self.compressed and not stored.startswith(ZSTD_MAGIC):
            return stored
        try:
            import zstandard
        except ImportError:
            self.notes.append("payload is compressed and the zstandard module is missing")
            return b""

        # compressed_size is the real stream length; payload_size is padded, and
        # feeding the padding to the decompressor is what makes naive readers
        # report a corrupt frame on a perfectly good entry.
        n = self.hdr.compressed_size or len(stored)
        try:
            out = zstandard.ZstdDecompressor().decompress(
                stored[:n], max_output_size=max(self.hdr.decompressed_size, 1 << 26))
        except Exception as exc:
            self.notes.append(f"decompression failed: {exc}")
            return b""
        if self.hdr.decompressed_size and len(out) != self.hdr.decompressed_size:
            self.notes.append(
                f"decompressed size mismatch: header says "
                f"{self.hdr.decompressed_size}, got {len(out)}")
        return out

    def arch_label(self):
        """The target this entry declares, suffix included.

        The suffix is the part a listing tends to drop: cuobjdump's own entry
        listing prints sm_89 for an entry whose flags say sm_89a, while its ELF
        dump prints sm_89a. Anything matching on the listing is therefore
        matching on a name the driver does not use.
        """
        prefix = "compute_" if self.kind == KIND_PTX else "sm_"
        return f"{prefix}{self.arch}{self.arch_suffix}"


def parse_container(blob, offset=0):
    """Parse one container at `offset`. Returns (FatBinHeader, [Entry]).

    The walk is bounded by the declared fat_size and additionally by the real
    buffer length, and it refuses a zero stride. Without those two guards a
    crafted container walks forever or off the end, which is precisely how a
    scanner gets turned into a denial of service by the files it inspects.
    """
    hdr = FatBinHeader.from_buffer_copy(bytes(blob[offset:offset + 16]))
    if not hdr.is_valid():
        raise ValueError(f"no fat binary magic at offset {offset:#x}")

    entries = []
    pos = offset + hdr.header_size
    end = min(offset + hdr.header_size + hdr.fat_size, len(blob))
    while pos + FIXED_ENTRY_HEADER <= end:
        eh = FatBinEntryHeader.from_buffer_copy(
            bytes(blob[pos:pos + FIXED_ENTRY_HEADER]))
        if eh.header_size < FIXED_ENTRY_HEADER:
            break
        entry = Entry(len(entries), pos, eh, blob)
        entries.append(entry)
        if entry.stride == 0:
            entry.notes.append("zero stride, walk stopped")
            break
        pos += entry.stride
    return hdr, entries


def walk_nv_fatbin(raw, start, size):
    """Yield the offset of every container in .nv_fatbin, by header chaining.

    Read the magic, then jump by header_size + fat_size to reach the next
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
        stride = hdr.header_size + hdr.fat_size
        if stride <= 0:
            break
        pos += stride


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

    if raw[:4] != b"\x7fELF":
        raise ValueError(f"{path}: neither a fat binary nor an ELF")

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
        fat_size = fatbin.header["sh_size"] if fatbin is not None else 0

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
            raise ValueError(f"{path}: no .nv_fatbin section")
        return
    for foff in walk_nv_fatbin(raw, fat_off, fat_size):
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

    Three levels, each consulted only when the one above it ties:

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
            "fat_size": hdr.fat_size,
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
                "ptx_isa": f"{e.hdr.version_major}.{e.hdr.version_minor}",
                "header_size": e.hdr.header_size,
                "payload_size": e.hdr.payload_size,
                "compressed": e.compressed,
                "compressed_size": e.hdr.compressed_size,
                "decompressed_size": e.hdr.decompressed_size,
                "flags": f"{e.flags:#x}",
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
              f"header {c['header_size']}, fat_size {c['fat_size']}, "
              f"{len(c['entries'])} entries, target {c['target']}, "
              f"policy {c['policy']}")
        print(f"   {'#':>2}  {'kind':<9} {'arch':<11} {'payload':>8} "
              f"{'comp':<5} {'flags':<10} {'sha256':<16} runs")
        for e in c["entries"]:
            print(f"   {e['index']:>2}  {e['kind_name']:<9} {e['arch_label']:<11} "
                  f"{e['payload_size']:>8} "
                  f"{'obf' if e['obfuscated'] else 'zstd' if e['compressed'] else '-':<5} "
                  f"{e['flags']:<10} {(e['payload_sha256'] or '')[:16]:<16} "
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

    containers = describe(args.file, sm, args.policy, suffix)
    if args.json:
        json.dump(containers, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print_report(containers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
