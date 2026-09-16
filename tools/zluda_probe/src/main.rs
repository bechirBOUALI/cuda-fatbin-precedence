// Thin harness over ZLUDA's own fat binary parser.
//
// Nothing about the container format is reimplemented here: the entries come
// from ZLUDA's `dark_api::fatbin`, at the pinned upstream commit. The two
// selection steps below are the ones `zluda/src/impl/module.rs` performs, kept
// deliberately literal:
//
//     if file.header.kind != FatbinFileHeader::HEADER_KIND_PTX { return; }
//     ...
//     ptx_modules.iter().rev()   // TODO: actually sort by SM
//
// ZLUDA additionally requires the winning PTX to parse before it accepts it;
// that step needs its compiler crates and is not reproduced. Where a container
// holds one parseable PTX entry, which is every case in the table this harness
// feeds, the answer is the same.
use cuda_types::dark_api::{FatbinFileHeader, FatbinHeader};
use dark_api::fatbin::FatbinFileIterator;
use std::{env, fs};

fn main() {
    let path = env::args().nth(1).expect("usage: zluda_probe <fatbin>");
    let bytes = fs::read(&path).expect("read");
    let header = unsafe { &*(bytes.as_ptr() as *const FatbinHeader) };

    let mut kept: Vec<(usize, usize)> = Vec::new();
    let mut index = 0usize;
    let mut it = unsafe { FatbinFileIterator::new(header) };
    while let Some(file) = unsafe { it.next() } {
        match file {
            Ok(f) => {
                if f.header.kind == FatbinFileHeader::HEADER_KIND_PTX {
                    if let Ok(text) = unsafe { f.get_or_decompress_content(true) } {
                        kept.push((index, text.len()));
                    }
                }
            }
            Err(_) => break,
        }
        index += 1;
    }

    match kept.last() {
        Some((i, len)) => println!("entry {}, the last PTX ({} bytes)", i, len),
        None => println!("nothing, every cubin is discarded"),
    }
}
