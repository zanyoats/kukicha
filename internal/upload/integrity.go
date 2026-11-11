package upload

import (
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

// ValidateNew initializes a new managed root only after confirming it contains
// no data. Local entries may be excluded by the ignore lists.
func ValidateNew(ctx context.Context, root app.Root, ignorePrefixes, ignoreFiles []string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	switch r := root.(type) {
	case *app.LocalRoot:
		if !filepath.IsAbs(r.Path) {
			return errors.New("storage location path must be absolute")
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
			return errors.New("storage location must be a real directory")
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
				if !app.IsIgnoredScanPath(entry.Name(), ignorePrefixes, ignoreFiles) {
					return fmt.Errorf("cannot initialize storage location %q: directory contains data (%q)", r.ConfigID(), entry.Name())
				}
			}
		}
		if err := ctx.Err(); err != nil {
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
		pages := s3.NewListObjectsV2Paginator(client, &s3.ListObjectsV2Input{
			Bucket: aws.String(r.Bucket), Prefix: aws.String(r.Prefix), MaxKeys: aws.Int32(1),
		})
		for pages.HasMorePages() {
			page, err := pages.NextPage(ctx)
			if err != nil {
				return err
			}
			if len(page.Contents) > 0 {
				return fmt.Errorf("cannot initialize storage location %q: prefix contains data (%q)", r.ConfigID(), aws.ToString(page.Contents[0].Key))
			}
		}
		return nil
	default:
		return fmt.Errorf("unsupported storage location %T", root)
	}
}

func syncDirectory(p string) error {
	f, err := os.Open(p)
	if err != nil {
		return err
	}
	return errors.Join(f.Sync(), f.Close())
}

// CheckRootStorage checks storage access without creating missing directories.
// Jobs must not recreate missing storage during recovery.
func CheckRootStorage(ctx context.Context, root app.Root) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	switch r := root.(type) {
	case *app.LocalRoot:
		info, err := os.Lstat(r.Path)
		if err != nil {
			return fmt.Errorf("storage location unavailable: %w", err)
		}
		if !info.IsDir() {
			return errors.New("storage location must be a real directory")
		}
		return nil
	case *app.RemoteRoot:
		client, err := walk.NewS3Client(ctx, r)
		if err != nil {
			return err
		}
		_, err = client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{Bucket: aws.String(r.Bucket), Prefix: aws.String(r.Prefix), MaxKeys: aws.Int32(1)})
		if err != nil {
			return fmt.Errorf("storage location unavailable: %w", err)
		}
		return nil
	default:
		return fmt.Errorf("unsupported storage location %T", root)
	}
}
