// Package metadata fetches explicitly selected album metadata from external providers.
package metadata

import (
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
)

var mbidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
var discogsIDPattern = regexp.MustCompile(`^([1-9][0-9]*)(?:-.*)?$`)

// ParseSource accepts public entity URLs, never arbitrary API or image endpoints.
func ParseSource(value string) (app.MetadataSource, error) {
	var s app.MetadataSource
	u, err := url.Parse(strings.TrimSpace(value))
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.Port() != "" {
		return s, fmt.Errorf("provide a valid Discogs or MusicBrainz HTTP(S) URL")
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) != 2 {
		return s, fmt.Errorf("expected an album entity URL with a type and ID")
	}
	host := strings.ToLower(u.Hostname())
	s.EntityType = parts[0]
	switch host {
	case "discogs.com", "www.discogs.com":
		s.Provider = "discogs"
		if s.EntityType != "release" && s.EntityType != "master" {
			return s, fmt.Errorf("Discogs URLs must identify a release or master")
		}
		match := discogsIDPattern.FindStringSubmatch(parts[1])
		if match == nil {
			return s, fmt.Errorf("Discogs entity ID must be a positive integer")
		}
		if _, err := strconv.ParseInt(match[1], 10, 64); err != nil {
			return s, fmt.Errorf("invalid Discogs entity ID")
		}
		s.EntityID = match[1]
		host = "www.discogs.com"
	case "musicbrainz.org", "www.musicbrainz.org":
		s.Provider = "musicbrainz"
		if s.EntityType != "release" && s.EntityType != "release-group" {
			return s, fmt.Errorf("MusicBrainz URLs must identify a release or release-group")
		}
		if !mbidPattern.MatchString(parts[1]) {
			return s, fmt.Errorf("MusicBrainz entity ID must be a UUID")
		}
		s.EntityID = strings.ToLower(parts[1])
		host = "musicbrainz.org"
	default:
		return s, fmt.Errorf("URL must belong to discogs.com or musicbrainz.org")
	}
	s.SourceURL = "https://" + host + "/" + s.EntityType + "/" + s.EntityID
	return s, nil
}
