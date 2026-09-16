// Thin harness over Datadog's own fat binary parser, pkg/gpu/cuda, at the
// pinned upstream commit. No parsing is reimplemented.
//
// That parser reads .nv_fatbin sections out of an ELF rather than a raw
// container, which is how it meets fat binaries in the wild, so the container
// is wrapped in an object file first with objcopy.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/DataDog/datadog-agent/pkg/gpu/cuda"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: dd_probe <fatbin> [sm]")
		os.Exit(2)
	}
	sm := uint32(89)
	dir, err := os.MkdirTemp("", "ddprobe")
	if err != nil {
		panic(err)
	}
	defer os.RemoveAll(dir)
	obj := filepath.Join(dir, "embedded.o")

	cmd := exec.Command("objcopy", "-I", "binary", "-O", "elf64-x86-64",
		os.Args[1], obj,
		"--rename-section", ".data=.nv_fatbin,alloc,load,readonly,data,contents")
	if out, err := cmd.CombinedOutput(); err != nil {
		fmt.Fprintf(os.Stderr, "objcopy: %v: %s\n", err, out)
		os.Exit(1)
	}

	accepted := map[uint32]struct{}{sm: {}}
	fb, err := cuda.ParseFatbinFromELFFilePath(obj, accepted)
	if err != nil {
		fmt.Printf("parse error: %v\n", err)
		return
	}
	if fb.NumKernels() == 0 {
		fmt.Println("no kernels found")
		return
	}
	for k := range fb.GetKernels() {
		fmt.Printf("kernel %q, %d bytes\n", k.Name, k.KernelSize)
	}
}
