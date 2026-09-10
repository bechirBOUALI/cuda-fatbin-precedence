CUDA_HOME := /usr/local/cuda-13.2
BUILD     := ../../build

.PHONY: all clean
all: $(BUILD)/loader

$(BUILD):
	mkdir -p $@

# Links against the link-time stub. At runtime the loader resolves
# libcuda.so.1 to the WSL-provided one via ldconfig. Do NOT put the stubs
# directory on LD_LIBRARY_PATH.
$(BUILD)/loader: loader.cpp | $(BUILD)
	g++ -O2 -Wall -Wextra -o $@ $< \
	  -I$(CUDA_HOME)/include \
	  -L$(CUDA_HOME)/lib64/stubs -lcuda

clean:
	rm -f $(BUILD)/loader
