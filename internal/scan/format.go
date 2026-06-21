package scan

import (
	"net/http"
	"strings"
)

const (
	formatID3v1  = "ID3v1"
	formatID3v22 = "ID3v2.2"
	formatID3v23 = "ID3v2.3"
	formatID3v24 = "ID3v2.4"
	formatMP4    = "MP4"
	formatVorbis = "VORBIS"
)

type embeddedArtwork struct {
	MIMEType string
	Data     []byte
}

func applyEmbeddedArtwork(record *TrackMetadata, artwork *embeddedArtwork) {
	if artwork == nil || len(artwork.Data) == 0 {
		return
	}

	record.HasEmbeddedArtwork = true
	record.ArtworkContentType = firstNonEmpty(artwork.MIMEType, sniffImageMIMEType(artwork.Data))
	record.ArtworkContentSize = len(artwork.Data)
	record.ArtworkBytes = artwork.Data
}

func sniffImageMIMEType(data []byte) string {
	if len(data) >= 12 && string(data[:4]) == "RIFF" && string(data[8:12]) == "WEBP" {
		return "image/webp"
	}
	if mime := http.DetectContentType(data); strings.HasPrefix(mime, "image/") {
		return mime
	}
	return ""
}
