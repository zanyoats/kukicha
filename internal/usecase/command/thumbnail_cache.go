package command

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	"image/png"

	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

//go:embed sql/thumbnail_cache_widths.sql
var thumbnailCacheWidthsSQL string

//go:embed sql/insert_thumbnail_cache.sql
var insertThumbnailCacheSQL string

const (
	AlbumArtwork64  = 64
	AlbumArtwork256 = 256
)

var errInvalidArtwork = errors.New("invalid artwork")

type thumbnailVariant struct {
	width       int
	height      int
	contentType string
	bytes       []byte
}

func CacheArtworkThumbnails(
	ctx context.Context,
	conn *sql.Conn,
	sourceBytes []byte,
	info, rootPath, path string,
) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if len(sourceBytes) == 0 {
		err := fmt.Errorf("%w: image data is empty", errInvalidArtwork)

		if logErr := InsertLibrarySyncLog(
			ctx, conn, "warn",
			fmt.Sprintf("cannot cache %s: %v", info, err), rootPath, path,
		); logErr != nil {
			return "", fmt.Errorf("%v: %w", err, logErr)
		}

		return "", nil
	}

	sum := sha256.Sum256(sourceBytes)
	name := fmt.Sprintf("%x", sum)

	existingWidths, err := cachedThumbnailWidths(ctx, conn, name)
	if err != nil {
		return "", err
	}
	if existingWidths[AlbumArtwork64] && existingWidths[AlbumArtwork256] {
		return name, nil
	}

	decoded, sourceFormat, err := image.Decode(bytes.NewReader(sourceBytes))
	if err != nil {
		err := fmt.Errorf("%w: decode image: %v", errInvalidArtwork, err)

		if logErr := InsertLibrarySyncLog(
			ctx, conn, "warn",
			fmt.Sprintf("cannot cache %s: %v", info, err), rootPath, path,
		); logErr != nil {
			return "", fmt.Errorf("%v: %w", err, logErr)
		}

		return "", nil
	}
	if decoded.Bounds().Dx() < 1 || decoded.Bounds().Dy() < 1 {
		err := fmt.Errorf("%w: image dimensions must be positive", errInvalidArtwork)

		if logErr := InsertLibrarySyncLog(
			ctx, conn, "warn",
			fmt.Sprintf("cannot cache %s: %v", info, err), rootPath, path,
		); logErr != nil {
			return "", fmt.Errorf("%v: %w", err, logErr)
		}

		return "", nil
	}

	variants := make([]thumbnailVariant, 0, 2)
	for _, width := range [...]int{AlbumArtwork64, AlbumArtwork256} {
		if existingWidths[width] {
			continue
		}

		thumbnail := resizeToWidth(decoded, width)
		artworkBytes, contentType, err := encodeThumbnail(
			thumbnail,
			sourceFormat,
		)
		if err != nil {
			return "", fmt.Errorf(
				"encode artwork at width %d: %w",
				width,
				err,
			)
		}

		variants = append(variants, thumbnailVariant{
			width:       width,
			height:      thumbnail.Bounds().Dy(),
			contentType: contentType,
			bytes:       artworkBytes,
		})
	}

	// Each variant is immutable and keyed by its content hash and width.
	// Conflict-safe independent writes permit interleaving on the scan connection.
	// A partial cache is repaired on the next attempt; association requires both sizes.

	for _, variant := range variants {
		if _, err := conn.ExecContext(
			ctx,
			insertThumbnailCacheSQL,
			sql.Named("name", name),
			sql.Named("width_px", variant.width),
			sql.Named("height_px", variant.height),
			sql.Named("artwork_content_type", variant.contentType),
			sql.Named("artwork_content_size", len(variant.bytes)),
			sql.Named("artwork_bytes", variant.bytes),
		); err != nil {
			return "", fmt.Errorf(
				"insert thumbnail cache width %d: %w",
				variant.width,
				err,
			)
		}
	}

	return name, nil
}

func cachedThumbnailWidths(
	ctx context.Context,
	conn *sql.Conn,
	name string,
) (map[int]bool, error) {
	rows, err := conn.QueryContext(
		ctx,
		thumbnailCacheWidthsSQL,
		sql.Named("name", name),
		sql.Named("artwork_width_small", AlbumArtwork64),
		sql.Named("artwork_width_large", AlbumArtwork256),
	)
	if err != nil {
		return nil, fmt.Errorf("query thumbnail cache: %w", err)
	}

	result := make(map[int]bool, 2)
	for rows.Next() {
		var width int
		if err := rows.Scan(&width); err != nil {
			_ = rows.Close()
			return nil, fmt.Errorf("scan thumbnail cache width: %w", err)
		}
		result[width] = true
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return nil, fmt.Errorf("iterate thumbnail cache widths: %w", err)
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("close thumbnail cache widths: %w", err)
	}

	return result, nil
}

func encodeThumbnail(
	src image.Image,
	sourceFormat string,
) ([]byte, string, error) {
	var encoded bytes.Buffer

	if sourceFormat == "jpeg" {
		err := jpeg.Encode(&encoded, src, &jpeg.Options{Quality: 90})
		return encoded.Bytes(), "image/jpeg", err
	}

	err := png.Encode(&encoded, src)
	return encoded.Bytes(), "image/png", err
}

func resizeToWidth(src image.Image, width int) image.Image {
	srcBounds := src.Bounds()
	height := int(
		float64(srcBounds.Dy())/float64(srcBounds.Dx())*float64(width) + 0.5,
	)
	if height < 1 {
		height = 1
	}

	dst := image.NewNRGBA(image.Rect(0, 0, width, height))
	draw.CatmullRom.Scale(dst, dst.Bounds(), src, srcBounds, draw.Over, nil)
	return dst
}
