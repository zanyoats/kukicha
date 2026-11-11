package walk

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"path"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awshttp "github.com/aws/aws-sdk-go-v2/aws/transport/http"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/zanyoats/kukicha/internal/app"
)

// RemoteWalkFunc handles object metadata and listing errors.
// On a listing error, only RelPath (the empty root-relative path) is populated.
// Returning nil skips the remainder of the listing; a non-nil error stops the walk.
// Context cancellation is returned directly.
type RemoteWalkFunc func(RemoteWalkedFile, error) error
type RemoteWalkedFile struct {
	Name, RelPath, Ext, Stem string
	FileSize                 int64
	FileModTime              time.Time
}

// Bound response-header waits without limiting the total duration of a
// large object download.
const remoteResponseHeaderTimeoutSec = 30

// NewS3Client creates an S3 client using the server's AWS credential chain.
// Select a named profile when adding or editing a remote root in the app.
// Shared configuration and credential helpers must be accessible to the server
// account; environment credentials must be set in the server process.
// Region must be supplied by the root or the server's AWS configuration.
func NewS3Client(ctx context.Context, root *app.RemoteRoot) (*s3.Client, error) {
	type loadOption = func(*config.LoadOptions) error
	var options []loadOption
	options = append(options,
		config.WithHTTPClient(awshttp.NewBuildableClient().WithTransportOptions(func(t *http.Transport) {
			t.ResponseHeaderTimeout = remoteResponseHeaderTimeoutSec * time.Second
		})),
	)
	if root.Profile != "" {
		options = append(options, config.WithSharedConfigProfile(root.Profile))
	}
	if root.Region != "" {
		options = append(options, config.WithRegion(root.Region))
	}
	cfg, err := config.LoadDefaultConfig(ctx, options...)
	if err != nil {
		return nil, err
	}
	if cfg.Region == "" {
		return nil, errors.New("no region configured: set region for this remote root or in AWS configuration")
	}
	return s3.NewFromConfig(cfg, func(o *s3.Options) {
		o.BaseEndpoint = aws.String(root.EndpointURL)
		o.UsePathStyle = root.AddressingStyle == "path"
	}), nil
}

// NewS3SourceClient uses only the submitted source settings. It deliberately does
// not load shared AWS configuration, environment credentials, or instance roles.
func NewS3SourceClient(ctx context.Context, source *app.RemoteSource) (*s3.Client, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := source.Normalize(); err != nil {
		return nil, err
	}
	credentials := source.Credentials
	return s3.New(s3.Options{
		Region:       source.Region,
		BaseEndpoint: aws.String(source.EndpointURL),
		UsePathStyle: source.AddressingStyle == "path",
		HTTPClient: awshttp.NewBuildableClient().WithTransportOptions(func(t *http.Transport) {
			t.ResponseHeaderTimeout = remoteResponseHeaderTimeoutSec * time.Second
		}),
		Credentials: aws.NewCredentialsCache(aws.CredentialsProviderFunc(func(context.Context) (aws.Credentials, error) {
			return aws.Credentials{AccessKeyID: credentials.AccessKeyID, SecretAccessKey: credentials.SecretAccessKey,
				SessionToken: credentials.SessionToken, Source: "submitted temporary source credentials"}, nil
		})),
	}), nil
}

// Bucket lists object metadata page by page. It never retrieves object bodies.
// Folder markers are skipped. RelPath removes prefix from the object key
// without cleaning or otherwise changing the remaining bytes.
func Bucket(
	ctx context.Context,
	client s3.ListObjectsV2APIClient,
	bucket,
	prefix string,
	fn RemoteWalkFunc,
) error {
	input := &s3.ListObjectsV2Input{
		Bucket: aws.String(bucket), Prefix: aws.String(prefix),
	}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		page, err := client.ListObjectsV2(ctx, input)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fn(RemoteWalkedFile{}, err)
		}
		for _, object := range page.Contents {
			if err := ctx.Err(); err != nil {
				return err
			}
			key := aws.ToString(object.Key)
			// There should never be a object in the bucket that points to an
			// empty path. This is a mere defensive check. However, we do want
			// to skip folder-like objects that end in `/`.
			if key == "" || strings.HasSuffix(key, "/") {
				continue
			}
			rel := strings.TrimPrefix(key, prefix)
			// Split without cleaning: empty and dot segments are part of the key.
			name := rel[strings.LastIndexByte(rel, '/')+1:]
			ext := path.Ext(name)
			file := RemoteWalkedFile{
				Name:        name,
				RelPath:     rel,
				Ext:         ext,
				Stem:        strings.TrimSpace(strings.TrimSuffix(name, ext)),
				FileSize:    aws.ToInt64(object.Size),
				FileModTime: aws.ToTime(object.LastModified),
			}
			if err := fn(file, nil); err != nil {
				return err
			}
		}
		if !aws.ToBool(page.IsTruncated) {
			return ctx.Err()
		}
		next := aws.ToString(page.NextContinuationToken)
		// Defensive checks to make sure we have a next continuation token
		// that is different from the current one.
		if next == "" || next == aws.ToString(input.ContinuationToken) {
			return fn(RemoteWalkedFile{}, fmt.Errorf("truncated S3 listing did not advance its continuation token"))
		}
		input.ContinuationToken = page.NextContinuationToken
	}
}
