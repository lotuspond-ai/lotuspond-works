//go:build !unix

package collector

import (
	"errors"
	"os"
)

// Only POSIX permissions are checked for; elsewhere the directory must at least be real.
func checkPrivateDirectory(dir string) error {
	info, err := os.Lstat(dir)
	if err != nil {
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return errors.New("COLLECTOR_SPOOL_DIR must be a directory, not a symbolic link")
	}
	return nil
}
