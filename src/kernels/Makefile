# Device-side artifacts for the fatbin entry-precedence experiment (Step 1).
#
# Produces, for each variant, a standalone .cubin and .ptx, then combines them
# into fatbins that deliberately contain MORE THAN ONE entry matching sm_89.
# The harness loads each fatbin and reports which marker came back, which tells
# us which entry the driver selected.

CUDA_HOME := /usr/local/cuda-13.2
NVCC      := $(CUDA_HOME)/bin/nvcc
FATBINARY := $(CUDA_HOME)/bin/fatbinary
SM        := 89
BUILD     := ../../build

VARIANTS := a b
CUBINS   := $(patsubst %,$(BUILD)/variant_%.cubin,$(VARIANTS))
PTXS     := $(patsubst %,$(BUILD)/variant_%.ptx,$(VARIANTS))
# An sm_86 cubin is binary compatible with this sm_89 GPU and runs on its own,
# so it is a genuine competing candidate rather than an entry the driver skips.
# That is what makes the architecture-proximity level of the hierarchy testable.
CUBINS86 := $(patsubst %,$(BUILD)/variant_%_sm86.cubin,$(VARIANTS))
CUBINS80 := $(patsubst %,$(BUILD)/variant_%_sm80.cubin,$(VARIANTS))
# sm_75 is a DIFFERENT major generation, so this cubin cannot run on sm_89 at
# all. It is the control that shows a first-match reading does not merely pick
# a suboptimal entry, it picks one the GPU would refuse.
CUBINS75 := $(BUILD)/variant_a_sm75.cubin
PTX75    := $(BUILD)/variant_b_c75.ptx
PATCH    := ../../scripts/patch_entry.py

# Conflict cases. Each holds two entries that both match sm_89.
FATBINS := $(BUILD)/elf_ab.fatbin \
           $(BUILD)/elf_ba.fatbin \
           $(BUILD)/ptxa_elfb.fatbin \
           $(BUILD)/elfb_ptxa.fatbin \
           $(BUILD)/ptx_ab.fatbin \
           $(BUILD)/ptx_ba.fatbin \
           $(BUILD)/elf86a_ptx89b.fatbin \
           $(BUILD)/ptx89b_elf86a.fatbin \
           $(BUILD)/elf89a_elf86b.fatbin \
           $(BUILD)/elf86b_elf89a.fatbin \
           $(BUILD)/elf80a_elf86b.fatbin \
           $(BUILD)/elf86b_elf80a.fatbin \
           $(BUILD)/elf_three.fatbin \
           $(BUILD)/elf_three_rev.fatbin \
           $(BUILD)/single_elf75.fatbin \
           $(BUILD)/elf75a_ptx75b.fatbin \
           $(BUILD)/ptx75b_elf75a.fatbin \
           $(BUILD)/single_elf_a.fatbin \
           $(BUILD)/flag20_elfa.fatbin \
           $(BUILD)/flag21_elfa.fatbin \
           $(BUILD)/flag24_elfa.fatbin

.PHONY: all clean list
all: $(CUBINS) $(CUBINS86) $(CUBINS80) $(CUBINS75) $(PTX75) $(PTXS) $(FATBINS)

$(BUILD):
	mkdir -p $@

$(BUILD)/variant_%.cubin: variant_%.cu | $(BUILD)
	$(NVCC) -arch=sm_$(SM) -cubin $< -o $@

$(BUILD)/variant_%.ptx: variant_%.cu | $(BUILD)
	$(NVCC) -arch=compute_$(SM) -ptx $< -o $@

$(BUILD)/variant_%_sm86.cubin: variant_%.cu | $(BUILD)
	$(NVCC) -arch=sm_86 -cubin $< -o $@

$(BUILD)/variant_%_sm80.cubin: variant_%.cu | $(BUILD)
	$(NVCC) -arch=sm_80 -cubin $< -o $@

$(BUILD)/variant_%_sm75.cubin: variant_%.cu | $(BUILD)
	$(NVCC) -arch=sm_75 -cubin $< -o $@

$(BUILD)/variant_%_c75.ptx: variant_%.cu | $(BUILD)
	$(NVCC) -arch=compute_75 -ptx $< -o $@

# Case 2: two ELF entries, same arch. A first.
$(BUILD)/elf_ab.fatbin: $(BUILD)/variant_a.cubin $(BUILD)/variant_b.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_a.cubin \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_b.cubin

# Case 2, order reversed. Comparing against elf_ab isolates whether
# precedence is positional or content-based.
$(BUILD)/elf_ba.fatbin: $(BUILD)/variant_a.cubin $(BUILD)/variant_b.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_b.cubin \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_a.cubin

# Case 1: PTX competing with ELF at the same arch, both orders.
$(BUILD)/ptxa_elfb.fatbin: $(BUILD)/variant_a.ptx $(BUILD)/variant_b.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_a.ptx \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_b.cubin

$(BUILD)/elfb_ptxa.fatbin: $(BUILD)/variant_a.ptx $(BUILD)/variant_b.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_b.cubin \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_a.ptx

# Level 3, PTX against PTX at the same arch, both orders. The winner is the
# LAST entry, the opposite of the ELF case above, which is why no single
# positional rule can be correct for both kinds.
$(BUILD)/ptx_ab.fatbin: $(BUILD)/variant_a.ptx $(BUILD)/variant_b.ptx
	$(FATBINARY) --create=$@ \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_a.ptx \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_b.ptx

$(BUILD)/ptx_ba.fatbin: $(BUILD)/variant_a.ptx $(BUILD)/variant_b.ptx
	$(FATBINARY) --create=$@ \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_b.ptx \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_a.ptx

# Level 1, kind outranks architecture. An sm_86 ELF against an EXACTLY matching
# compute_89 PTX. If kind decides first, the worse-matching ELF wins anyway.
$(BUILD)/elf86a_ptx89b.fatbin: $(BUILD)/variant_a_sm86.cubin $(BUILD)/variant_b.ptx
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_a_sm86.cubin \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_b.ptx

$(BUILD)/ptx89b_elf86a.fatbin: $(BUILD)/variant_a_sm86.cubin $(BUILD)/variant_b.ptx
	$(FATBINARY) --create=$@ \
	  --image3=kind=ptx,sm=$(SM),file=$(BUILD)/variant_b.ptx \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_a_sm86.cubin

# Level 2, architecture proximity among entries of one kind, both orders.
$(BUILD)/elf89a_elf86b.fatbin: $(BUILD)/variant_a.cubin $(BUILD)/variant_b_sm86.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_a.cubin \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_b_sm86.cubin

$(BUILD)/elf86b_elf89a.fatbin: $(BUILD)/variant_a.cubin $(BUILD)/variant_b_sm86.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_b_sm86.cubin \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_a.cubin

# Level 2 again, with neither entry matching exactly. This is the shape real
# libraries ship: cuBLAS carries no sm_89 cubin, so on this GPU the nearest
# compatible one wins. Both orders.
$(BUILD)/elf80a_elf86b.fatbin: $(BUILD)/variant_a_sm80.cubin $(BUILD)/variant_b_sm86.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=80,file=$(BUILD)/variant_a_sm80.cubin \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_b_sm86.cubin

$(BUILD)/elf86b_elf80a.fatbin: $(BUILD)/variant_a_sm80.cubin $(BUILD)/variant_b_sm86.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_b_sm86.cubin \
	  --image3=kind=elf,sm=80,file=$(BUILD)/variant_a_sm80.cubin

# Three entries, to check the rule is not an artefact of testing pairs. The
# sm_86 entry should win from either end.
$(BUILD)/elf_three.fatbin: $(CUBINS75) $(BUILD)/variant_a_sm80.cubin $(BUILD)/variant_b_sm86.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=75,file=$(BUILD)/variant_a_sm75.cubin \
	  --image3=kind=elf,sm=80,file=$(BUILD)/variant_a_sm80.cubin \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_b_sm86.cubin

$(BUILD)/elf_three_rev.fatbin: $(CUBINS75) $(BUILD)/variant_a_sm80.cubin $(BUILD)/variant_b_sm86.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=86,file=$(BUILD)/variant_b_sm86.cubin \
	  --image3=kind=elf,sm=80,file=$(BUILD)/variant_a_sm80.cubin \
	  --image3=kind=elf,sm=75,file=$(BUILD)/variant_a_sm75.cubin

# Control: an sm_75 cubin alone is refused on this GPU, which is what makes
# "first match picks sm_75" a real error rather than a preference.
$(BUILD)/single_elf75.fatbin: $(CUBINS75)
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=75,file=$(BUILD)/variant_a_sm75.cubin

# Compatibility is checked BEFORE the kind preference, not after. The sm_75
# cubin cannot run on this GPU, so it leaves the candidate set and the PTX
# entry wins by default, even though ELF outranks PTX. This is the inverse of
# the ptxa_elfb case and it is what makes the generation check load-bearing
# rather than cosmetic.
$(BUILD)/elf75a_ptx75b.fatbin: $(CUBINS75) $(PTX75)
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=75,file=$(BUILD)/variant_a_sm75.cubin \
	  --image3=kind=ptx,sm=75,file=$(BUILD)/variant_b_c75.ptx

$(BUILD)/ptx75b_elf75a.fatbin: $(CUBINS75) $(PTX75)
	$(FATBINARY) --create=$@ \
	  --image3=kind=ptx,sm=75,file=$(BUILD)/variant_b_c75.ptx \
	  --image3=kind=elf,sm=75,file=$(BUILD)/variant_a_sm75.cubin

# Flag-bit exclusion. One valid sm_89 ELF entry, then single bits of the entry
# flags field set. Bits 20 and 21 remove the entry from the driver's candidate
# set; bit 24 is the control that does not. Every size and offset is untouched.
$(BUILD)/single_elf_a.fatbin: $(BUILD)/variant_a.cubin
	$(FATBINARY) --create=$@ \
	  --image3=kind=elf,sm=$(SM),file=$(BUILD)/variant_a.cubin

$(BUILD)/flag%_elfa.fatbin: $(BUILD)/single_elf_a.fatbin
	python3 $(PATCH) $< $@ --entry 0 --set-bit $*

# Payload tampering, to test whether the container or the driver validates
# entry contents. Substitutes the marker immediate inside the compiled SASS of
# a single valid entry: two bytes change, every size, offset and magic stays
# correct. Not part of the precedence corpus, since it asks about integrity
# rather than selection.
$(BUILD)/tampered_payload.fatbin: $(BUILD)/single_elf_a.fatbin
	python3 -c "import sys; \
	d=bytearray(open('$<','rb').read()); \
	i=d.find(bytes.fromhex('02780500aaaa0000000f')); \
	sys.exit('marker encoding not found') if i<0 else None; \
	d[i+4:i+6]=b'\xcc\xcc'; \
	open('$@','wb').write(bytes(d)); \
	print('$@: payload marker 0xAAAA -> 0xCCCC at offset %#x' % (i+4))"

.PHONY: tamper
tamper: $(BUILD)/tampered_payload.fatbin

# Flag bit 24 in the ELF-against-ELF tie-break. Two sm_89 cubins, A then B, so
# the baseline winner is A. Setting bit 24 on A alone should hand the win to B,
# because the entry lacking the bit wins; setting it on B alone should change
# nothing. Predicted from the ranker's decompiled tail, then measured.
$(BUILD)/b24_on_first.fatbin: $(BUILD)/elf_ab.fatbin
	python3 $(PATCH) $< $@ --entry 0 --set-bit 24

$(BUILD)/b24_on_second.fatbin: $(BUILD)/elf_ab.fatbin
	python3 $(PATCH) $< $@ --entry 1 --set-bit 24

# What a static scanner reports for each artifact. Compare against what the
# harness observes at runtime; any disagreement is the finding.
list: $(FATBINS)
	@for f in $(FATBINS); do \
	  echo "== $$f"; $(CUDA_HOME)/bin/cuobjdump -lelf -lptx $$f; \
	done

clean:
	rm -f $(CUBINS) $(CUBINS86) $(CUBINS80) $(CUBINS75) $(PTX75) $(PTXS) $(FATBINS)
