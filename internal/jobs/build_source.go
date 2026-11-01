package jobs

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/walk"
)

// scanSource helps working on remote and local file sources.
type scanSource interface {
	Name() string
	Prepare(context.Context, logicalFile) (preparedFile, error)
}

type preparedFile struct {
	Path    string
	Release func() error
}

type localSource struct {
	path string
}

func (s *localSource) Name() string { return s.path }

func (s *localSource) Prepare(ctx context.Context, file logicalFile) (preparedFile, error) {
	if err := ctx.Err(); err != nil {
		return preparedFile{}, err
	}
	physicalPath := filepath.Join(s.path, file.RelPath)
	info, err := os.Lstat(physicalPath)
	if err != nil {
		return preparedFile{}, err
	}
	if !info.Mode().IsRegular() {
		return preparedFile{}, fmt.Errorf("source is not a regular file: %q", physicalPath)
	}
	return preparedFile{
		Path:    physicalPath,
		Release: func() error { return nil },
	}, nil
}

type remoteSource struct {
	source  *app.RemoteSource
	client  *s3.Client
	tempDir string
}

func newRemoteSource(ctx context.Context, source *app.RemoteSource) (*remoteSource, error) {
	client, err := walk.NewS3SourceClient(ctx, source)
	if err != nil {
		return nil, fmt.Errorf("configure remote source: %w", err)
	}
	dir, err := os.MkdirTemp("", "kukicha-remote-*")
	if err != nil {
		return nil, err
	}
	return &remoteSource{source: source, client: client, tempDir: dir}, nil
}

func (s *remoteSource) Name() string { return "s3://" + s.source.Bucket + "/" + s.source.Prefix }

func (s *remoteSource) Prepare(ctx context.Context, file logicalFile) (_ preparedFile, err error) {
	if err := ctx.Err(); err != nil {
		return preparedFile{}, err
	}
	download, err := os.CreateTemp(s.tempDir, "object-*"+file.Ext)
	if err != nil {
		return preparedFile{}, err
	}
	release := func() error { return os.Remove(download.Name()) }
	// Failed preparation retains ownership of the partial download.
	defer func() {
		if err != nil {
			err = errors.Join(err, release())
		}
	}()
	response, err := s.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.source.Bucket),
		Key:    aws.String(s.source.Prefix + file.RelPath),
	})
	if err != nil {
		return preparedFile{}, errors.Join(err, download.Close())
	}
	_, copyErr := io.Copy(download, response.Body)
	if err := errors.Join(copyErr, response.Body.Close(), download.Close()); err != nil {
		return preparedFile{}, err
	}
	return preparedFile{Path: download.Name(), Release: release}, nil
}
