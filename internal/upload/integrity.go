package upload

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/walk"
)

const rootMarkerName = ".kukicha_root"

// ValidateNew initializes a new managed root only after confirming it contains
// no data or existing root marker. Local entries may be excluded by the ignore lists.
func ValidateNew(ctx context.Context, root app.Root, ignorePrefixes, ignoreFiles []string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	switch r := root.(type) {
	case *app.LocalRoot:
		if !filepath.IsAbs(r.Path) {
			return errors.New("root path must be absolute")
		}
		created := false
		info, err := os.Lstat(r.Path)
		if errors.Is(err, os.ErrNotExist) {
			if err := os.MkdirAll(filepath.Dir(r.Path), 0700); err != nil {
				return err
			}
			if err := os.Mkdir(r.Path, 0700); err != nil {
				return err
			}
			created = true
		} else if err != nil {
			return err
		} else if !info.IsDir() {
			return errors.New("root must be a real directory")
		}
		dir, err := os.OpenRoot(r.Path)
		if err != nil {
			return err
		}
		defer dir.Close()
		if !created {
			f, err := dir.Open(".")
			if err != nil {
				return err
			}
			entries, readErr := f.ReadDir(-1)
			if err := errors.Join(readErr, f.Close()); err != nil {
				return err
			}
			for _, entry := range entries {
				if err := ctx.Err(); err != nil {
					return err
				}
				if entry.Name() == rootMarkerName {
					return fmt.Errorf("cannot initialize root %q: root marker already exists", r.ConfigID())
				}
				if !app.IsIgnoredScanPath(entry.Name(), ignorePrefixes, ignoreFiles) {
					return fmt.Errorf("cannot initialize root %q: directory contains data (%q)", r.ConfigID(), entry.Name())
				}
			}
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		f, err := dir.OpenFile(rootMarkerName, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		if err := errors.Join(f.Sync(), f.Close()); err != nil {
			return err
		}
		if err := syncDirectory(r.Path); err != nil {
			return err
		}
		if created {
			return syncDirectory(filepath.Dir(r.Path))
		}
		return nil
	case *app.RemoteRoot:
		client, err := walk.NewS3Client(ctx, r)
		if err != nil {
			return err
		}
		key := r.Prefix + rootMarkerName
		pages := s3.NewListObjectsV2Paginator(client, &s3.ListObjectsV2Input{
			Bucket: aws.String(r.Bucket), Prefix: aws.String(r.Prefix), MaxKeys: aws.Int32(1),
		})
		for pages.HasMorePages() {
			page, err := pages.NextPage(ctx)
			if err != nil {
				return err
			}
			if len(page.Contents) > 0 {
				return fmt.Errorf("cannot initialize root %q: prefix contains data (%q)", r.ConfigID(), aws.ToString(page.Contents[0].Key))
			}
		}
		if _, err := client.PutObject(ctx, &s3.PutObjectInput{Bucket: aws.String(r.Bucket), Key: aws.String(key), Body: bytes.NewReader(nil), IfNoneMatch: aws.String("*")}); err != nil {
			return err
		}
		_, err = client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(r.Bucket), Key: aws.String(key)})
		return err
	default:
		return fmt.Errorf("unsupported root %T", root)
	}
}

func syncDirectory(p string) error {
	f, err := os.Open(p)
	if err != nil {
		return err
	}
	return errors.Join(f.Sync(), f.Close())
}

// CheckRootMarker validates an existing destination without initializing it.
// Jobs must not recreate missing storage during recovery.
func CheckRootMarker(ctx context.Context, root app.Root) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	switch r := root.(type) {
	case *app.LocalRoot:
		info, err := os.Lstat(r.Path)
		if err != nil {
			return fmt.Errorf("root unavailable: %w", err)
		}
		if !info.IsDir() {
			return errors.New("root must be a real directory")
		}
		info, err = os.Lstat(filepath.Join(r.Path, rootMarkerName))
		if err != nil {
			return fmt.Errorf("root unavailable: %s marker required: %w", rootMarkerName, err)
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("root unavailable: %s must be a regular file", rootMarkerName)
		}
		return nil
	case *app.RemoteRoot:
		client, err := walk.NewS3Client(ctx, r)
		if err != nil {
			return err
		}
		_, err = client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(r.Bucket), Key: aws.String(r.Prefix + rootMarkerName)})
		if err != nil {
			return fmt.Errorf("root marker unavailable: %w", err)
		}
		return nil
	default:
		return fmt.Errorf("unsupported root %T", root)
	}
}
