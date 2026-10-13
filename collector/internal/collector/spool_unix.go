//go:build unix

package collector

import (
	"errors"
	"os"
	"syscall"
)

// The spool holds workspace evidence, so only the collector's own user may reach it.
func checkPrivateDirectory(dir string) error {
	info, err := os.Lstat(dir)
	if err != nil {
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return errors.New("COLLECTOR_SPOOL_DIR must be a directory, not a symbolic link")
	}
	if info.Mode().Perm()&0o077 != 0 {
		return errors.New("COLLECTOR_SPOOL_DIR must be accessible only by the collector's user (chmod 700)")
	}
	if stat, ok := info.Sys().(*syscall.Stat_t); ok && int(stat.Uid) != os.Getuid() {
		return errors.New("COLLECTOR_SPOOL_DIR must be owned by the collector's user")
	}
	return nil
}
