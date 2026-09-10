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

# Conflict cases. Each holds two entries that both match sm_89.
FATBINS := $(BUILD)/elf_ab.fatbin \
           $(BUILD)/elf_ba.fatbin \
           $(BUILD)/ptxa_elfb.fatbin \
           $(BUILD)/elfb_ptxa.fatbin

.PHONY: all clean list
all: $(CUBINS) $(PTXS) $(FATBINS)

$(BUILD):
	mkdir -p $@

$(BUILD)/variant_%.cubin: variant_%.cu | $(BUILD)
	$(NVCC) -arch=sm_$(SM) -cubin $< -o $@

$(BUILD)/variant_%.ptx: variant_%.cu | $(BUILD)
	$(NVCC) -arch=compute_$(SM) -ptx $< -o $@

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

# What a static scanner reports for each artifact. Compare against what the
# harness observes at runtime; any disagreement is the finding.
list: $(FATBINS)
	@for f in $(FATBINS); do \
	  echo "== $$f"; $(CUDA_HOME)/bin/cuobjdump -lelf -lptx $$f; \
	done

clean:
	rm -f $(CUBINS) $(PTXS) $(FATBINS)
