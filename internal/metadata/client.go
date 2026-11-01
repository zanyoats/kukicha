package metadata

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	_ "golang.org/x/image/webp"
)

// Client is used sequentially by a build, with separate pacing for each provider.
type Client struct {
	HTTP            *http.Client
	nextRequest     time.Time
	lookupInterval  time.Duration
	maxAttempts     int
	artworkMaxBytes int64
}

func NewClient(config app.ProviderLookupConfig, artworkMaxBytes int64) *Client {
	return &Client{
		lookupInterval:  config.LookupInterval.Duration,
		maxAttempts:     config.MaxAttempts,
		artworkMaxBytes: artworkMaxBytes,
		HTTP: &http.Client{
			Timeout: config.RequestTimeout.Duration,
			CheckRedirect: func(r *http.Request, via []*http.Request) error {
				if len(via) >= 10 {
					return errors.New("too many redirects")
				}
				return validateRemoteURL(r.URL)
			},
		},
	}
}

func validateRemoteURL(u *url.URL) error {
	if u.Scheme != "https" || u.User != nil || u.Port() != "" {
		return errors.New("unsupported external URL")
	}
	host := strings.ToLower(u.Hostname())
	for _, domain := range []string{"api.discogs.com", "discogs.com", "disco.gs", "musicbrainz.org", "coverartarchive.org", "archive.org"} {
		if host == domain || strings.HasSuffix(host, "."+domain) {
			return nil
		}
	}
	return fmt.Errorf("unsupported external host %q", host)
}

// get retries rate limits and temporary service failures. Every attempt is bounded
// by the configured timeout and response size; waiting respects cancellation.
func (c *Client) get(ctx context.Context, rawURL string, maxBytes int64) ([]byte, int, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, 0, err
	}
	if err := validateRemoteURL(u); err != nil {
		return nil, 0, err
	}
	for attempt := 0; ; attempt++ {
		if delay := time.Until(c.nextRequest); delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return nil, 0, ctx.Err()
			case <-timer.C:
			}
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
		if err != nil {
			return nil, 0, err
		}
		req.Header.Set("User-Agent", app.UserAgent)
		c.nextRequest = time.Now().Add(c.lookupInterval)
		resp, err := c.HTTP.Do(req)
		if err != nil {
			return nil, 0, err
		}
		if resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode == http.StatusServiceUnavailable {
			delay := max(c.lookupInterval, time.Second)
			value := strings.TrimSpace(resp.Header.Get("Retry-After"))
			if seconds, err := time.ParseDuration(value + "s"); err == nil && seconds >= 0 {
				delay = max(delay, seconds)
			} else if deadline, err := http.ParseTime(value); err == nil {
				delay = max(delay, time.Until(deadline))
			}
			c.nextRequest = time.Now().Add(delay)
			resp.Body.Close()
			if attempt+1 < c.maxAttempts {
				continue
			}
			return nil, resp.StatusCode, fmt.Errorf("external request returned HTTP %d", resp.StatusCode)
		}
		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			return nil, resp.StatusCode, fmt.Errorf("external request returned HTTP %d", resp.StatusCode)
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes+1))
		resp.Body.Close()
		if err != nil {
			return nil, resp.StatusCode, err
		}
		if int64(len(data)) > maxBytes {
			return nil, resp.StatusCode, fmt.Errorf("external response exceeds %d bytes", maxBytes)
		}
		return data, resp.StatusCode, nil
	}
}

func (c *Client) Fetch(ctx context.Context, s app.MetadataSource) (app.MetadataOverride, error) {
	result := app.MetadataOverride{MetadataSource: s}
	var endpoint string
	if s.Provider == "discogs" {
		kind := "releases"
		if s.EntityType == "master" {
			kind = "masters"
		}
		endpoint = "https://api.discogs.com/" + kind + "/" + s.EntityID
	} else {
		endpoint = "https://musicbrainz.org/ws/2/" + s.EntityType + "/" + s.EntityID + "?fmt=json&inc=genres"
	}
	data, _, err := c.get(ctx, endpoint, app.LookupMaxBytes)
	if err != nil {
		return result, err
	}
	if s.Provider == "discogs" {
		var payload struct {
			ID     int64    `json:"id"`
			Genres []string `json:"genres"`
			Styles []string `json:"styles"`
			Images []struct {
				Type string `json:"type"`
				URI  string `json:"uri"`
			} `json:"images"`
		}
		if err := json.Unmarshal(data, &payload); err != nil {
			return result, err
		}
		if fmt.Sprint(payload.ID) != s.EntityID {
			return result, errors.New("Discogs response has an unexpected entity ID")
		}
		result.Genres = cleanValues(append(payload.Genres, payload.Styles...))
		for _, img := range payload.Images {
			if img.URI != "" && (result.ArtworkURL == "" || img.Type == "primary") {
				result.ArtworkURL = img.URI
			}
			if img.Type == "primary" && img.URI != "" {
				break
			}
		}
		result.ArtworkComplete = result.ArtworkURL == ""
	} else {
		var payload struct {
			ID     string `json:"id"`
			Genres []struct {
				Name string `json:"name"`
			} `json:"genres"`
		}
		if err := json.Unmarshal(data, &payload); err != nil {
			return result, err
		}
		if payload.ID != s.EntityID {
			return result, errors.New("MusicBrainz response has an unexpected entity ID")
		}
		for _, genre := range payload.Genres {
			result.Genres = append(result.Genres, genre.Name)
		}
		result.Genres = cleanValues(result.Genres)
		result.ArtworkURL = "https://coverartarchive.org/" + s.EntityType + "/" + s.EntityID + "/front-500"
	}
	return result, nil
}

// Artwork reports missing CAA artwork separately from retryable fetch failures.
func (c *Client) Artwork(ctx context.Context, rawURL string) (data []byte, missing bool, err error) {
	data, status, err := c.get(ctx, rawURL, c.artworkMaxBytes)
	u, _ := url.Parse(rawURL)
	if status == http.StatusNotFound && u != nil && u.Hostname() == "coverartarchive.org" {
		return nil, true, nil
	}
	if err != nil {
		return nil, false, err
	}
	if len(data) == 0 {
		return nil, false, errors.New("artwork response was empty")
	}
	contentType := http.DetectContentType(data)
	if !strings.HasPrefix(contentType, "image/") {
		return nil, false, fmt.Errorf("artwork response has unsupported content type %q", contentType)
	}
	cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, false, fmt.Errorf("decode cover dimensions: %w", err)
	}
	if cfg.Width < app.ArtworkMinimumWidth {
		return nil, false, fmt.Errorf("artwork is %d pixels wide; minimum is %d", cfg.Width, app.ArtworkMinimumWidth)
	}
	return data, false, nil
}

func cleanValues(values []string) []string {
	result := make([]string, 0, len(values))
	seen := make(map[string]bool)
	for _, value := range values {
		value = strings.TrimSpace(value)
		key := strings.ToLower(value)
		if value != "" && !seen[key] {
			seen[key] = true
			result = append(result, value)
		}
	}
	return result
}
