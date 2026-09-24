package walk

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var ErrWalkPathNotDirectory = errors.New("path expected to be a directory/folder")

// WalkFunc handles regular files and directory read errors.
// When err is non-nil, only RelPath is populated.
// Returning nil skips that directory and continues the walk.
// Otherwise, file describes a regular file; FileSize and FileModTime
// are valid only when file.StatErr is nil.
// Returning a non-nil error stops the walk.
type WalkFunc func(LocalWalkedFile, error) error
type LocalWalkedFile struct {
	Name, RelPath, Ext, Stem string
	FileSize                 int64
	FileModTime              time.Time
	StatErr                  error
}

// Dir recursively walks regular files beneath path.
// It skips symlinks and rejects a symlink root.
// Root validation errors are passed to fn with an empty RelPath.
// Returning nil from fn skips that root.
func Dir(path string, fn WalkFunc) error {
	info, err := os.Lstat(path)
	if err != nil {
		return fn(LocalWalkedFile{RelPath: ""}, err)
	}
	if !info.IsDir() {
		return fn(LocalWalkedFile{RelPath: ""}, fmt.Errorf(
			"%w: %q",
			ErrWalkPathNotDirectory,
			path,
		))
	}

	return walkDir(path, "", fn)
}

func walkDir(dirPath, childDirPath string, fn WalkFunc) error {
	fullPath := filepath.Join(dirPath, childDirPath)
	files, err := os.ReadDir(fullPath)
	if err != nil {
		if err := fn(LocalWalkedFile{RelPath: childDirPath}, err); err != nil {
			return err
		}
		return nil
	}

	for _, file := range files {
		switch {
		case file.Type().IsRegular():
			walked := LocalWalkedFile{}
			walked.Name = file.Name()
			walked.RelPath = filepath.Join(childDirPath, walked.Name)
			walked.Ext = filepath.Ext(walked.Name)
			walked.Stem = strings.TrimSpace(
				strings.TrimSuffix(walked.Name, walked.Ext),
			)

			if info, err := file.Info(); err == nil {
				walked.FileSize = info.Size()
				walked.FileModTime = info.ModTime()
			} else {
				walked.StatErr = err
			}

			if err := fn(walked, nil); err != nil {
				return err
			}

		case file.Type().IsDir():
			newChildDirPath := filepath.Join(childDirPath, file.Name())
			if err := walkDir(dirPath, newChildDirPath, fn); err != nil {
				return err
			}
		}
	}

	return nil
}
