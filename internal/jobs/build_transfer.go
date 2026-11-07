package jobs

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"

	"github.com/aws/aws-sdk-go-v2/aws"
	awshttp "github.com/aws/aws-sdk-go-v2/aws/transport/http"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/walk"
)

// buildTarget is shared by workers. Destination writes are always exclusive;
// existing S3 objects are reusable only after their contents are verified.
type buildTarget struct {
	local  *os.Root
	remote *app.RemoteRoot
	client *s3.Client
}

func newBuildTarget(ctx context.Context, root app.Root) (*buildTarget, error) {
	if err := upload.CheckRootMarker(ctx, root); err != nil {
		return nil, err
	}
	target := &buildTarget{}
	var err error
	switch r := root.(type) {
	case *app.LocalRoot:
		target.local, err = os.OpenRoot(r.Path)
	case *app.RemoteRoot:
		target.remote = r
		target.client, err = walk.NewS3Client(ctx, r)
	default:
		err = fmt.Errorf("unsupported build target %T", root)
	}
	return target, err
}

func (t *buildTarget) Close() {
	if t.local != nil {
		t.local.Close()
	}
}

func (t *buildTarget) Copy(ctx context.Context, source, relative string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := upload.ValidateRelativePath(relative); err != nil {
		return err
	}
	info, err := os.Lstat(source)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("source is not a regular file: %q", source)
	}
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	opened, err := input.Stat()
	if err != nil {
		return err
	}
	if !os.SameFile(info, opened) {
		return fmt.Errorf("source changed: %q", source)
	}
	if t.local != nil {
		name := filepath.FromSlash(relative)
		if err := t.local.MkdirAll(filepath.Dir(name), 0700); err != nil {
			return err
		}
		output, err := t.local.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(output, buildContextReader{ctx, input})
		return errors.Join(copyErr, output.Close())
	}
	key := t.remote.Prefix + relative
	_, err = t.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:      aws.String(t.remote.Bucket),
		Key:         aws.String(key),
		Body:        input,
		IfNoneMatch: aws.String("*"),
	})
	var responseErr *awshttp.ResponseError
	if errors.As(err, &responseErr) && responseErr.HTTPStatusCode() == http.StatusPreconditionFailed {
		return t.verifyExistingObject(ctx, key, input)
	}
	return err
}

func (t *buildTarget) verifyExistingObject(ctx context.Context, key string, input *os.File) error {
	if _, err := input.Seek(0, io.SeekStart); err != nil {
		return fmt.Errorf("rewind source to verify S3 key %q: %w", key, err)
	}
	object, err := t.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(t.remote.Bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return fmt.Errorf("read existing S3 key %q: %w", key, err)
	}
	defer object.Body.Close()

	storedHash := sha256.New()
	if _, err := io.Copy(storedHash, object.Body); err != nil {
		return fmt.Errorf("verify contents of S3 key %q: %w", key, err)
	}
	sourceHash := sha256.New()
	if _, err := io.Copy(sourceHash, buildContextReader{ctx, input}); err != nil {
		return fmt.Errorf("read source to verify S3 key %q: %w", key, err)
	}
	if !bytes.Equal(sourceHash.Sum(nil), storedHash.Sum(nil)) {
		return fmt.Errorf("conflicting S3 key %q: existing object differs from source", key)
	}
	return nil
}

type buildContextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r buildContextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(p)
}
