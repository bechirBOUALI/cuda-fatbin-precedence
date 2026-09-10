#!/usr/bin/python
import ctypes
import sys

from elftools.elf.elffile import ELFFile

"""
The parser starts form: ELF -> FatBinWrapper -> FatBinHeader -> FatBinEntry

"""

# Names and values below are NVIDIA's own, from the toolkit header
#   /usr/local/cuda-<ver>/include/fatbinary_section.h
FATBINC_MAGIC        = 0x466243B1   # 'FbC\xb1', found in .nvFatBinSegment
FATBINC_VERSION      = 1            # filename_or_fatbins is an offline filename
FATBINC_LINK_VERSION = 2            # filename_or_fatbins is an array of prelinked fatbins
FATBIN_HEADER_MAGIC  = 0xBA55ED50   # found in .nv_fatbin


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


def iter_wrappers(blob: bytearray):
    """Yield every __fatBinC_Wrapper_t in a .nvFatBinSegment blob.

    from_buffer() maps the struct ONTO the bytearray rather than copying it,
    so assigning to a field edits `blob` in place. That is what makes this
    usable for building fat binaries with conflicting entries, not just
    reading them.
    """
    size = ctypes.sizeof(FatBinCWrapper)
    for off in range(0, len(blob) - size + 1, size):
        w = FatBinCWrapper.from_buffer(blob, off)
        if w.is_valid():
            yield off, w


class BinInfo:
    pass

def extract_FatbinEntries(filename: str) -> list:
    "return list of FatbinWrappers"
    wrappers = []
    with open(filename, 'rb') as f:
        for sect in ELFFile(f).iter_sections():
            if sect.name != ".nvFatBinSegment":
                continue
            print(
                f'  Note section "{sect.name}" at offset '
                f"0x{sect.header['sh_offset']:08x} with size "
                f"{sect.header['sh_size']:d}"
            )
            blob = bytearray(sect.data())
            for off, w in iter_wrappers(blob):
                print(f"    +{off:#06x}  {w}")
                wrappers.append(w)
    return wrappers


# TODO(you): the wrapper's .data is a virtual address. To reach the fatbin
# bytes you need VA -> file offset. Find the section whose
# sh_addr <= data < sh_addr + sh_size (it will be .nv_fatbin), then
# file_offset = sh_offset + (data - sh_addr).

# TODO(you): FatBinHeader  -- u32 magic, u16 version, u16 headerSize, u64 fatSize
# TODO(you): FatBinEntry   -- layout is in analysis/driver-selection-logic.md;
#            note PTX payloads are zstd-compressed, ELF payloads are raw.

def unpack_FatBinEntry(data: bytes) -> BinInfo:
    pass


def main():
    extract_FatbinEntries(sys.argv[1])

if __name__ == '__main__':
    main()