package web

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func parseAlbumID(r *http.Request) (int64, error) {
	return parsePositiveID(r, "album_id")
}

func parseTrackID(r *http.Request) (int64, error) {
	return parsePositiveID(r, "track_id")
}

func parseAlbumFilters(r *http.Request) (query.AlbumFilters, error) {
	var filters query.AlbumFilters
	values := r.URL.Query()
	for _, filter := range []struct {
		name string
		id   *int64
	}{
		{"artist_id", &filters.ArtistID},
		{"genre_id", &filters.GenreID},
		{"style_id", &filters.StyleID},
	} {
		if !values.Has(filter.name) {
			continue
		}
		id, err := parsePositiveID(r, filter.name)
		if err != nil {
			return query.AlbumFilters{}, err
		}
		*filter.id = id
	}
	return filters, nil
}

func parseArtworkSize(r *http.Request) (query.AlbumArtworkSize, error) {
	value, err := requiredQueryValue(
		r.URL.Query(),
		"size",
	)
	if err != nil {
		return "", err
	}

	switch value {
	case string(query.AlbumArtwork64):
		return query.AlbumArtwork64, nil

	case string(query.AlbumArtwork256):
		return query.AlbumArtwork256, nil

	default:
		return "", errors.New("size must be thumb or full")
	}
}

func parsePositiveID(
	r *http.Request,
	name string,
) (int64, error) {
	value, err := requiredQueryValue(
		r.URL.Query(),
		name,
	)
	if err != nil {
		return 0, err
	}

	id, err := strconv.ParseInt(value, 10, 64)
	if err != nil || id < 1 {
		return 0, fmt.Errorf(
			"%s must be a positive integer",
			name,
		)
	}

	return id, nil
}

func requiredQueryValue(
	values url.Values,
	name string,
) (string, error) {
	found := values[name]

	if len(found) != 1 || found[0] == "" {
		return "", fmt.Errorf(
			"a single %s is required",
			name,
		)
	}

	return found[0], nil
}
