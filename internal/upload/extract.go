package upload

import (
	"archive/tar"
	"archive/zip"
	"compress/bzip2"
	"compress/gzip"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"mime"
	"mime/multipart"
	"os"
	"path"
	"path/filepath"
	"strings"
)

const MaxRequestBytes int64 = 2 << 30

// Archives are discarded after extraction. Nested archives are retained as
// ordinary files, never recursively expanded. Other archive formats are retained as files.

type extractor struct {
	ctx      context.Context
	dir      *os.Root
	hasFiles bool
}

func safePath(name string) error { return ValidateRelativePath(name) }

// ValidateRelativePath checks paths shared by extraction and build transfer.
func ValidateRelativePath(name string) error {
	if !fs.ValidPath(name) || name == "." ||
		strings.ContainsAny(name, `\:`) {
		return fmt.Errorf("invalid relative path %q", name)
	}
	return nil
}

func (e *extractor) directory(name string) error {
	if err := e.ctx.Err(); err != nil {
		return err
	}
	if err := safePath(name); err != nil {
		return err
	}
	return e.dir.MkdirAll(filepath.FromSlash(name), 0700)
}

func (e *extractor) file(name string, src io.Reader) error {
	if err := e.ctx.Err(); err != nil {
		return err
	}
	if err := safePath(name); err != nil {
		return err
	}
	if err := e.dir.MkdirAll(filepath.FromSlash(path.Dir(name)), 0700); err != nil {
		return err
	}
	f, err := e.dir.OpenFile(filepath.FromSlash(name), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(f, contextReader{e.ctx, src})
	if err := errors.Join(copyErr, f.Close()); err != nil {
		return err
	}
	e.hasFiles = true
	return nil
}

type contextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r contextReader) Read(b []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(b)
}

func extractParts(ctx context.Context, staging string, parts *multipart.Reader) error {
	dir, err := os.OpenRoot(staging)
	if err != nil {
		return err
	}
	defer dir.Close()
	e := extractor{ctx: ctx, dir: dir}
	for {
		part, err := parts.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return err
		}
		_, params, err := mime.ParseMediaType(part.Header.Get("Content-Disposition"))
		if err != nil {
			part.Close()
			return err
		}
		name := params["filename"]
		if part.FormName() != "files" || name == "" {
			part.Close()
			return errors.New("expected a files part with a relative filename")
		}
		if err := safePath(name); err != nil {
			part.Close()
			return err
		}
		err = e.part(name, part)
		closeErr := part.Close()
		if err := errors.Join(err, closeErr); err != nil {
			return err
		}
	}
	if !e.hasFiles {
		return errors.New("upload contains no files")
	}
	return nil
}

func (e *extractor) part(name string, reader io.Reader) (resultErr error) {
	lower := strings.ToLower(name)
	switch {
	case strings.HasSuffix(lower, ".zip"):
		// ZIP needs random access. Keep its input in the upload
		// temp directory and remove it after extraction.
		temp := "._kukicha-archive-" + newID()
		f, err := e.dir.OpenFile(temp, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		defer func() { resultErr = errors.Join(resultErr, e.dir.Remove(temp)) }()
		n, err := io.Copy(f, contextReader{e.ctx, reader})
		if err != nil {
			return errors.Join(err, f.Close())
		}
		z, err := zip.NewReader(f, n)
		if err == nil {
			for _, entry := range z.File {
				if entry.Mode().IsDir() {
					err = e.directory(strings.TrimSuffix(entry.Name, "/"))
				} else if !entry.Mode().IsRegular() {
					err = fmt.Errorf("archive entry %q: links and special files are not allowed", entry.Name)
				} else {
					var src io.ReadCloser
					src, err = entry.Open()
					if err == nil {
						err = errors.Join(e.file(entry.Name, src), src.Close())
					}
				}
				if err != nil {
					break
				}
			}
		}
		return errors.Join(err, f.Close())
	case strings.HasSuffix(lower, ".tar.gz"), strings.HasSuffix(lower, ".tgz"):
		gz, err := gzip.NewReader(contextReader{e.ctx, reader})
		if err != nil {
			return err
		}
		err = e.tar(gz)
		return errors.Join(err, gz.Close())
	case strings.HasSuffix(lower, ".tar.bz2"), strings.HasSuffix(lower, ".tbz"), strings.HasSuffix(lower, ".tbz2"):
		return e.tar(bzip2.NewReader(contextReader{e.ctx, reader}))
	case strings.HasSuffix(lower, ".tar"):
		return e.tar(reader)
	default:
		return e.file(name, reader)
	}
}

func (e *extractor) tar(reader io.Reader) error {
	input := contextReader{e.ctx, reader}
	tr := tar.NewReader(input)
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			// Finish reading so compression trailer/checksum errors surface.
			_, err = io.Copy(io.Discard, input)
			return err
		}
		if err != nil {
			return err
		}
		switch h.Typeflag {
		case tar.TypeDir:
			err = e.directory(strings.TrimSuffix(h.Name, "/"))
		case tar.TypeReg:
			err = e.file(h.Name, tr)
		default:
			err = fmt.Errorf("archive entry %q: links and special entries are not allowed", h.Name)
		}
		if err != nil {
			return err
		}
	}
}

func newID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}
