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
	contentType string
	bytes       []byte
}

func cacheArtworkThumbnails(
	ctx context.Context,
	conn *sql.Conn,
	sourceBytes []byte,
) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if len(sourceBytes) == 0 {
		return "", fmt.Errorf("%w: image data is empty", errInvalidArtwork)
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
		return "", fmt.Errorf("%w: decode image: %v", errInvalidArtwork, err)
	}
	if decoded.Bounds().Dx() < 1 || decoded.Bounds().Dy() < 1 {
		return "", fmt.Errorf("%w: image dimensions must be positive", errInvalidArtwork)
	}

	variants := make([]thumbnailVariant, 0, 2)
	for _, width := range [...]int{AlbumArtwork64, AlbumArtwork256} {
		if existingWidths[width] {
			continue
		}

		artworkBytes, contentType, err := encodeThumbnail(
			resizeToWidth(decoded, width),
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
			contentType: contentType,
			bytes:       artworkBytes,
		})
	}

	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return "", fmt.Errorf("begin thumbnail cache update: %w", err)
	}

	for _, variant := range variants {
		if _, err := tx.ExecContext(
			ctx,
			insertThumbnailCacheSQL,
			sql.Named("name", name),
			sql.Named("width_px", variant.width),
			sql.Named("artwork_content_type", variant.contentType),
			sql.Named("artwork_content_size", len(variant.bytes)),
			sql.Named("artwork_bytes", variant.bytes),
		); err != nil {
			_ = tx.Rollback()
			return "", fmt.Errorf(
				"insert thumbnail cache width %d: %w",
				variant.width,
				err,
			)
		}
	}

	if err := tx.Commit(); err != nil {
		return "", fmt.Errorf("commit thumbnail cache update: %w", err)
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
