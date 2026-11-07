package jobs

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var ErrJobNotCleanable = errors.New("Cleanup requires a failed or canceled addition that did not publish its library results.")

// CleanupBuild runs in the HTTP request. Terminal, unpublished additions own
// unique output prefixes that no later build or rescan consumes. They are never
// requeued, so deletion needs no coordination with the build processor.
func CleanupBuild(ctx context.Context, db *sql.DB, conf *app.AppConfig, id int64) error {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()
	job, err := query.JobByID(ctx, db, id)
	if err != nil {
		return err
	}
	if !job.IsBuildAdd() || (job.State != "failed" && job.State != "canceled") || job.LibraryPublished {
		return ErrJobNotCleanable
	}
	if job.CleanedAt.Valid {
		return nil
	}
	if err := cleanupBuildFiles(ctx, conf, job); err != nil {
		return err
	}
	// If this write fails or the request disconnects, retrying deletion is safe.
	_, err = db.ExecContext(ctx, "UPDATE jobs SET cleaned_at = COALESCE(cleaned_at, CURRENT_TIMESTAMP) WHERE id = ?", id)
	if err != nil {
		return fmt.Errorf("files removed, but cleanup confirmation could not be saved; retry cleanup: %w", err)
	}
	return nil
}

// Missing output is success, including when requests overlap or retry a partial deletion.
func cleanupBuildFiles(ctx context.Context, conf *app.AppConfig, job query.Job) error {
	var p app.BuildJobPayload
	if err := json.Unmarshal(job.Payload, &p); err != nil {
		return err
	}
	var destination app.Root
	for _, root := range conf.Roots {
		if root.ConfigID() == p.RootID {
			destination = root
			break
		}
	}
	if destination == nil || upload.Location(destination) != p.RootLocation {
		return errors.New("build destination configuration changed; restore the original destination before retrying cleanup")
	}
	relative, err := buildDestinationPath(job.CreatedAt, job.ID)
	if err != nil {
		return err
	}
	target, err := newBuildTarget(ctx, destination)
	if err != nil {
		return err
	}
	defer target.Close()
	if err := ctx.Err(); err != nil {
		return err
	}
	if target.local != nil {
		name := filepath.FromSlash(strings.TrimSuffix(relative, "/"))
		// Refuse symlinked output directories instead of following them during removal.
		parent, err := target.local.Lstat(filepath.Dir(name))
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err != nil {
			return err
		}
		if !parent.IsDir() {
			return errors.New("build output parent is not a real directory")
		}
		info, err := target.local.Lstat(name)
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err != nil {
			return err
		}
		if !info.IsDir() {
			return errors.New("build output is not a real directory")
		}
		if err := target.local.RemoveAll(name); err != nil {
			return err
		}
		if _, err := target.local.Lstat(name); !errors.Is(err, os.ErrNotExist) {
			if err != nil {
				return err
			}
			return errors.New("build output still exists after cleanup")
		}
		return nil
	}
	prefix := target.remote.Prefix + relative
	// Re-list the first batch after each deletion. This also verifies emptiness
	// without relying on continuation tokens across a changing object listing.
	for {
		page, err := target.client.ListObjectsV2(ctx, &s3.ListObjectsV2Input{
			Bucket: aws.String(target.remote.Bucket), Prefix: aws.String(prefix), MaxKeys: aws.Int32(1000),
		})
		if err != nil {
			return fmt.Errorf("list remaining build output: %w", err)
		}
		if len(page.Contents) == 0 {
			return nil
		}
		objects := make([]types.ObjectIdentifier, 0, len(page.Contents))
		for _, object := range page.Contents {
			if object.Key == nil || !strings.HasPrefix(*object.Key, prefix) {
				return errors.New("storage returned an object outside the build output prefix")
			}
			objects = append(objects, types.ObjectIdentifier{Key: object.Key})
		}
		result, err := target.client.DeleteObjects(ctx, &s3.DeleteObjectsInput{
			Bucket: aws.String(target.remote.Bucket), Delete: &types.Delete{Objects: objects},
		})
		if err != nil {
			return fmt.Errorf("remove build output: %w", err)
		}
		if len(result.Errors) != 0 {
			failure := result.Errors[0]
			return fmt.Errorf("could not remove %q: %s: %s (%d deletion errors)",
				aws.ToString(failure.Key), aws.ToString(failure.Code), aws.ToString(failure.Message), len(result.Errors))
		}
	}
}
