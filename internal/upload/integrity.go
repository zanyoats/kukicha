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
	"github.com/aws/smithy-go"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/walk"
)

const rootMarkerName = ".kukicha_root"

// validateRoot only requires a top-level marker. Its contents are deliberately
// ignored: this does not verify library contents or detect replacement storage.
// The caller holds the service lock during root initialization.
func validateRoot(ctx context.Context, root app.Root) error {
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
		if created {
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
			if err := syncDirectory(filepath.Dir(r.Path)); err != nil {
				return err
			}
		}
		info, err = dir.Lstat(rootMarkerName)
		if err != nil {
			return fmt.Errorf("root unavailable: top-level %s marker required: %w", rootMarkerName, err)
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
		key := r.Prefix + rootMarkerName
		_, err = client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(r.Bucket), Key: aws.String(key)})
		if err == nil {
			return nil
		}
		var api smithy.APIError
		if !errors.As(err, &api) || (api.ErrorCode() != "NotFound" && api.ErrorCode() != "NoSuchKey") {
			return fmt.Errorf("root marker unavailable: %w", err)
		}
		// Only a confirmed empty prefix may be initialized. Access errors are
		// not evidence of emptiness; never add a marker to populated storage.
		out, err := client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{Bucket: aws.String(r.Bucket), Prefix: aws.String(r.Prefix), MaxKeys: aws.Int32(1)})
		if err != nil {
			return err
		}
		if len(out.Contents) != 0 || aws.ToBool(out.IsTruncated) {
			return fmt.Errorf("cannot initialize root %q: storage already contains objects but is missing the %s marker; choose an empty bucket prefix", r.ConfigID(), rootMarkerName)
		}
		_, err = client.PutObject(ctx, &s3.PutObjectInput{Bucket: aws.String(r.Bucket), Key: aws.String(key), Body: bytes.NewReader(nil), IfNoneMatch: aws.String("*")})
		if err != nil {
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
