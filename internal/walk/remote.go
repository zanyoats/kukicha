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

// TODO Explicit Go STS integration with additional config keys
//   role_arn = "arn:aws:iam::ACCOUNT_ID:role/MusicReader"
//   sts_endpoint_url = "https://sts.example.com"

// NewS3Client creates an S3 client using AWS SDK credential configuration.
//
// Shared files and external helpers must be accessible to the OS user running
// the server; environment variables must be set in the server's environment.
//
// A region must be either set in the remote root configuration or in the profile
// used by the remote root at ~/.aws/config.
//
// Below are the 3 methods of providing authentication.
//
// # (1) Shared credentials profile
//
// A named profile in ~/.aws/credentials supplies the credentials:
//
//	[myprofile]
//	aws_access_key_id = YOUR_ACCESS_KEY
//	aws_secret_access_key = YOUR_SECRET_KEY
//
// The matching profile in ~/.aws/config can supply the region:
//
//	[profile myprofile]
//	region = us-east-1
//
// Select the profile in the app's configuration:
//
//	[[scan.roots]]
//	id = "myname"
//	type = "remote"
//	endpoint_url = "https://s3.example.com"
//	bucket = "com.example.music"
//	prefix = "tracks/"
//	profile = "myprofile"
//
// # (2) Environment variables
//
// Set these in the environment that starts the server:
//
//	export AWS_ACCESS_KEY_ID='YOUR_ACCESS_KEY'
//	export AWS_SECRET_ACCESS_KEY='YOUR_SECRET_KEY'
//	export AWS_REGION='us-east-1'
//
// Leave profile unset in the app's configuration to use environment credentials:
//
//	[[scan.roots]]
//	id = "myname"
//	type = "remote"
//	endpoint_url = "https://s3.example.com"
//	bucket = "com.example.music"
//	prefix = "tracks/"
//
// # (3) External credential process
//
// Configure an executable credential helper in ~/.aws/config:
//
//	[profile myprofile]
//	credential_process = /absolute/path/to/credential-helper
//	region = us-east-1
//
// The helper must exit successfully and write only credential JSON to stdout:
//
//	{
//	  "Version": 1,
//	  "AccessKeyId": "YOUR_ACCESS_KEY",
//	  "SecretAccessKey": "YOUR_SECRET_KEY"
//	}
//
// Select the helper's profile in the app's configuration:
//
//	[[scan.roots]]
//	id = "myname"
//	type = "remote"
//	endpoint_url = "https://s3.example.com"
//	bucket = "com.example.music"
//	prefix = "tracks/"
//	profile = "myprofile"
//
// There is support for temporary credentials but it must be handled by the
// eternal credential process for now.
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
