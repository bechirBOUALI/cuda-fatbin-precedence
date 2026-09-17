# Decompiled C for the selection path

Companion to `driver-selection-logic.md`, which gives the same findings as
disassembly. This file gives them as C, which is easier to read and harder to
misread.

Produced with Ghidra 12.1.2 headless against
`/usr/lib/wsl/drivers/nvltwi.inf_amd64_508a7ec7f027b810/libcuda.so.1.1`,
driver 597.06, the same binary and build as every other result here. Full
auto-analysis found all three functions at the entry points the disassembly had
already identified. A second run with analysis disabled, creating functions
from `.eh_frame` boundaries instead, produced identical control flow, casts and
constants, which is worth knowing because it means none of the C below depends
on Ghidra's guesses about function extents.

Addresses in comments are **virtual addresses**, which equal file offsets in
this image. Ghidra's own labels, the `LAB_0057xxxx` and `switchD_00574aeb_*`
names left in the output, carry Ghidra addresses instead, which are the virtual
address plus the 0x100000 image base.

## How to read this, and how much to trust it

The excerpts are Ghidra output with identifiers renamed and comments added.
Nothing else was changed: control flow, casts and every constant are as the
decompiler emitted them.

Two warnings, because they matter for reading the C at all.

**Pointer arithmetic is scaled, and the scale differs per function.** Ghidra
picked a different element type for the entry pointer in each function, so the
same field appears as `entry[5]`, `entry + 0xe` or `entry + 0x14` depending on
where you are reading. Every such access is annotated with its real byte
offset. The constants are trustworthy; the types are not.

**The filter's return value is misrendered.** Ghidra types it `ulong` and
builds it with `CONCAT71`, which makes the function look like it returns a
handle. It returns a boolean in `AL`. The walk treats it as one.

Constants below were re-checked against `objdump` independently of the
decompiler: the container and entry field offsets, the jump table contents, the
suffix strings, the `snprintf` buffer size and truncation branch, and all five
call sites between the three functions.

## The walk: how the loop is bounded, and by what

```c
        container = state[0xb];   // container = state->[0x58]  (state is long*, 0xb*8 = 0x58)   VA 0x47b687
        fatbin_size = *(undefined8 *)(container + 8);   // fatbin_size = u64 @ container+0x08   VA 0x47b68f
        first_entry = (uint *)((ulong)*(ushort *)(container + 6) + container);
        // first entry = container + (u16 header_size @ container+0x06)   VA 0x47b68b
        if ((int)fatbin_size < 1) {
LAB_0057be94:
          state[0xd] = 0;
```

and the loop advances by a stride it takes from each entry:

```c
            group_entry = group_entry_next;
            entry = (uint *)((long)entry + (ulong)entry[1] + *(long *)(entry + 2));
            // stride = (u32 header_size @ +0x04) + (u64 padded_payload_size @ +0x08)   VA 0x47b6e6/0x47b6ea
          } while ((long)entry - (long)first_entry < (long)(int)fatbin_size);
          // walk until fatbin_size bytes consumed   VA 0x47b6f1/0x47b6fe
          if (incumbent == (uint *)0x0) goto LAB_0057be94;
```

Three things are worth naming. The container's own header size is a u16 at
offset 6 and its entry-array size is at offset 8, so a parser that walks the
same way the driver does reads those two fields and nothing else. The entry
stride is `header_size + padded_payload_size` read out of the entry itself, which is
why assuming a fixed entry size is wrong. And the loop bound is compared as
`(long)(int)fatbin_size`, a genuine 32-bit truncation of the container's declared
size and not a decompiler artifact; the instruction is `movslq %esi,%rax` at
`0x47b6f1`.

The loop body also handles a kind `0x100` group entry with a nested walk. That
path is out of scope here, which is about precedence among ordinary entries,
and it is omitted rather than summarised.

Between them the two functions below are called from five sites: the filter at
`0x47b6ce` and `0x47b77f`, the ranker at `0x47b7dd`, `0x47b8ed` and `0x47b990`.

## The filter: the architecture is a name

This is the default policy path, and it is the clearest evidence for what flag
bits 20 and 21 are.

```c
    default:
switchD_00574aeb_caseD_0:   // default target   VA 0x474b40
      flags_lo = (uint)*(ulong *)(entry + 0x14);
      // flags = u64 @ entry+0x28  (entry is ushort*, so +0x14 == +0x28 bytes)   VA 0x474b40
      arch_or_target_h = (ulong)*(uint *)(entry + 0xe);   // arch = u32 @ entry+0x1c   VA 0x474b44
      flags_bit20 = flags_lo & 0x100000;   // isolate flags bit 20   VA 0x474b4f
      if (*(uint *)(entry + 0xe) - 1 < 999) {   // bounds-check arch to 1..999 (cmp $0x3e6 / ja)   VA 0x474b59
        suffix = "a";   // VA 0x1510be8
        if ((*(ulong *)(entry + 0x14) & 0x100000) == 0) {
LAB_00574d00:
          suffix = "";   // VA 0x15150d7
          flags_bit20 = 0;
          if ((flags_lo & 0x200000) != 0) {
            flags_bit20 = 0;
            suffix = "f";   // VA 0x1515639, selected on flags bit 21   VA 0x474d00
          }
        }
        snprintf(name_buf,0xd,"%s_%d%s",&DAT_0136f4d8,arch_or_target_h,suffix);   // &DAT_0136f4d8 is "sm" @ VA 0x126f4d8
      }
      else {
LAB_00574cec:
        name_buf[0] = '\0';
      }
      arch_or_target_h = parse_target(name_buf);   // parse "sm_<arch><suffix>"; arch_or_target_h now holds a handle, not an arch
      if (arch_or_target_h != 0) {
```

The entry's architecture is not compared as a number. It is formatted into a
string, `sm_<arch><suffix>`, and that string is then parsed back into a target
handle which is what selection actually compares. The suffix comes from the
flags: `"a"` for bit 20, `"f"` for bit 21, `""` for neither. So an entry does
not carry an architecture, it carries an architecture *name*, and two bits of
its flags field change which name.

The buffer is 13 bytes and a would-be truncation is rejected, `cmp $0xc` then
`jg` at `0x474c3c`. With the architecture bounded to 1..999 the longest name is
`sm_999a`, so that branch is defensive rather than reachable.

Also visible here, and not something the black-box measurements had shown: a
required-flags mask at `state+0x10` which the entry's flags must contain, and a
separate canonicalisation step applied only to PTX entries.

## The ranker: the precedence order, in source form

```c
  if (kind_tmp != 2) {
    cand_kind = *candidate;
    if (cand_kind == 2) {
      return candidate;
    }
    if (kind_tmp == 0x10) {
      if (cand_kind != 0x10) {
        return incumbent;
      }
    }
    else {
      if (cand_kind == 0x10) {
        return candidate;
      }
      if (kind_tmp == 1) {
        if (cand_kind != 1) {
          return incumbent;
        }
      }
      else {
        if (cand_kind == 1) {
          return candidate;
        }
        if ((kind_tmp == 0x80) && (cand_kind == 0x80)) {
          cand_arch = state_helper(state);
          if (cand_arch != 0) {
            return incumbent;
          }
          inc_flag = (**(code **)(state + 0x148))(*(undefined4 *)(incumbent + 0xc),*(undefined4 *)(candidate + 0xc));
          if (inc_flag != '\0') {
            return candidate;
          }
          return incumbent;
        }
      }
    }
    if (*(int *)(state + 8) == *(int *)(candidate + 0xe)) {
      return candidate;
    }
    return incumbent;
  }
```

Read the returns and the order falls out. An ELF candidate beats any
non-ELF incumbent. Kind `0x10` beats everything except ELF. PTX beats
everything except those two. Anything else is unranked and loses to all three.
That is `ELF > 0x10 > PTX > rest`, and because each kind is tested on both
sides before anything else is considered, no file order can overturn it.

After the kind cascade comes the architecture, and the first test is an exact
match against the requested target at `state+8`, which is the level below kind
in the hierarchy and above file order.

## The ranker's tail: how two equal cubins are separated

Where the kind cascade above does not apply, because both entries are ELF, the
function falls through to this:

```c
  if (*candidate != 2) {
    return incumbent;
  }
  if ((*(ulong *)(candidate + 0x14) & 0x1000000) == 0) {
    if ((*(ulong *)(incumbent + 0x14) & 0x1000000) == 0) goto LAB_00575318;
  }
  else if ((*(ulong *)(incumbent + 0x14) & 0x1000000) == 0) {
    return incumbent;
  }
  if ((*(ulong *)(candidate + 0x14) & 0x1000000) == 0) {
    return candidate;
  }
LAB_00575318:
  make_ident(id_inc,incumbent);   // final tie-break: compare the u16 at offset 6 of two identifier structs
  make_ident(id_cand,candidate);
  if (id_cand_f6 <= id_inc_f6) {
    candidate = incumbent;
  }
  return candidate;
```

Flag bit 24 is `0x1000000`. Follow the branches: if exactly one of the two
entries has it, the one **without** it is returned. Only when both agree on
that bit does the final comparison run, on a u16 taken from an identifier
structure built for each entry, and the candidate has to be strictly greater to
displace the incumbent. Ties therefore go to the incumbent, which is the
earlier entry, and that is the "first ELF wins" behaviour measured
black-box.

This predicted something the measurements had not covered. Two sm_89 cubins in
the order A then B: A normally wins by being first, so setting bit 24 on A
alone should hand the win to B, while setting it on B alone should change
nothing. Both predictions hold on the GPU, and the two containers are in the
corpus as `b24_on_first.fatbin` and `b24_on_second.fatbin`. The toolkit sets
bit 24 on targets of compute capability 100 and above, so it is ordinary
metadata rather than a reserved bit.

## Identifier maps

The complete decompilation of all three functions, both runs, with a full
identifier map per function and a verified rename-only guarantee, is not
reproduced here for length. The renames used above are:

| Ghidra | Here | Meaning |
|---|---|---|
| `state[0xb]`, `*(long *)(state + 0x58)` | `container` | the fat binary container base |
| `puVar*` at the loop cursor | `entry` | current entry |
| `puVar*` held across iterations | `incumbent` | the winner so far |
| `param_3` | `candidate` | the entry being offered to the ranker |
| `*(int *)(state + 0xc)` | `policy_or_stride` | selector policy, reused later for a stride |
| `uVar4` | `arch_or_target_h` | the entry architecture, then the parsed target handle |
| `FUN_00574aa0` | `entry_is_acceptable` | the filter |
| `FUN_00574f20` | `pick_better_entry` | the ranker |
| `FUN_0059db10` | `parse_target` | behavioural name, not from the decompiler |

Callee names ending in `_target` are inferences from behaviour, not recovered
symbols. The binary is stripped and has none.
