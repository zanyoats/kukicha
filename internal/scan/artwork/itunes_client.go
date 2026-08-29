package artwork

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	_ "golang.org/x/image/webp"
)

type HTTPDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type lookupRateLimitError struct {
	retryAfter    time.Duration
	hasRetryAfter bool
}

func (e *lookupRateLimitError) Error() string {
	if e.hasRetryAfter {
		return fmt.Sprintf(
			"iTunes lookup returned HTTP %d (retry after %s)",
			http.StatusTooManyRequests,
			e.retryAfter,
		)
	}

	return fmt.Sprintf(
		"iTunes lookup returned HTTP %d",
		http.StatusTooManyRequests,
	)
}

type artworkRateLimitError struct {
	retryAfter    time.Duration
	hasRetryAfter bool
}

func (e *artworkRateLimitError) Error() string {
	if e.hasRetryAfter {
		return fmt.Sprintf(
			"artwork URL returned HTTP %d (retry after %s)",
			http.StatusTooManyRequests,
			e.retryAfter,
		)
	}

	return fmt.Sprintf(
		"artwork URL returned HTTP %d",
		http.StatusTooManyRequests,
	)
}

type ITunesClient struct {
	http           HTTPDoer
	userAgent      string
	lookupGate     chan struct{}
	lookupInterval time.Duration
	retryAttempts  int
	nextLookup     time.Time
}

type ITunesClientConfig struct {
	RequestTimeout time.Duration
	LookupInterval time.Duration
	RetryAttempts  int
}

func NewITunesClient(
	httpClient HTTPDoer,
	config ITunesClientConfig,
) *ITunesClient {
	if httpClient == nil {
		httpClient = &http.Client{
			Timeout: config.RequestTimeout,
		}
	}
	if config.LookupInterval < 0 {
		config.LookupInterval = 0
	}
	if config.RetryAttempts < 1 {
		config.RetryAttempts = 1
	}

	lookupGate := make(chan struct{}, 1)
	lookupGate <- struct{}{}

	return &ITunesClient{
		http:           httpClient,
		userAgent:      app.UserAgent,
		lookupGate:     lookupGate,
		lookupInterval: config.LookupInterval,
		retryAttempts:  config.RetryAttempts,
	}
}

func (c *ITunesClient) LookupArtwork(
	ctx context.Context,
	kind app.ITunesLookupKind,
	lookupID string,
) (app.ITunesLookupResult, error) {
	result := app.ITunesLookupResult{
		LookupKind: kind,
		LookupID:   lookupID,
	}

	lookupURL, err := buildITunesLookupURL(kind, lookupID)
	if err != nil {
		return result, err
	}
	result.LookupURL = lookupURL

	payload, missing, err := c.fetchLookup(ctx, lookupURL)
	if err != nil {
		return result, err
	}
	if missing {
		result.ResultKind = app.ITunesResultMissing
		return result, nil
	}

	imageURL, advertisedWidth, found, err := selectITunesArtworkURL(payload)
	if err != nil {
		return result, err
	}
	if !found {
		result.ResultKind = app.ITunesResultMissing
		return result, nil
	}

	imageBytes, contentType, resolvedURL, err :=
		c.fetchArtworkImage(ctx, imageURL, advertisedWidth)
	if err != nil {
		return result, err
	}

	result.ResultKind = app.ITunesResultHit
	result.ArtworkURL = resolvedURL
	result.ContentType = contentType
	result.Bytes = imageBytes

	return result, nil
}

func (c *ITunesClient) fetchLookup(
	ctx context.Context,
	lookupURL string,
) ([]byte, bool, error) {
	for attempt := 0; ; attempt++ {
		request, err := http.NewRequestWithContext(
			ctx,
			http.MethodGet,
			lookupURL,
			nil,
		)
		if err != nil {
			return nil, false, err
		}

		request.Header.Set("Accept", "application/json")
		request.Header.Set("User-Agent", c.userAgent)

		if err := c.acquireLookup(ctx); err != nil {
			return nil, false, fmt.Errorf(
				"wait for iTunes lookup rate limit: %w",
				err,
			)
		}

		response, err := c.http.Do(request)
		if err != nil {
			c.releaseLookup()
			return nil, false, fmt.Errorf(
				"fetch iTunes lookup: %w",
				err,
			)
		}

		if response.StatusCode == http.StatusTooManyRequests {
			retryAfter, hasRetryAfter := parseRetryAfter(
				response.Header.Get("Retry-After"),
				time.Now(),
			)
			if hasRetryAfter {
				c.deferLookup(retryAfter)
			}
			c.releaseLookup()
			response.Body.Close()

			if hasRetryAfter && attempt+1 < c.retryAttempts {
				continue
			}

			return nil, false, &lookupRateLimitError{
				retryAfter:    retryAfter,
				hasRetryAfter: hasRetryAfter,
			}
		}

		c.releaseLookup()
		defer response.Body.Close()

		if response.StatusCode == http.StatusNotFound {
			return nil, true, nil
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			return nil, false, fmt.Errorf(
				"iTunes lookup returned HTTP %d",
				response.StatusCode,
			)
		}

		payload, err := readBoundedResponse(
			response.Body,
			app.ItunesLookupMaxBytes,
		)
		if err != nil {
			return nil, false, fmt.Errorf(
				"read iTunes lookup response: %w",
				err,
			)
		}

		return payload, false, nil
	}
}

// acquireLookup serializes lookup requests and spaces their start times. The
// gate remains held until releaseLookup so a Retry-After response can extend
// the delay before another caller begins waiting.
func (c *ITunesClient) acquireLookup(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-c.lookupGate:
	}

	delay := time.Until(c.nextLookup)
	if delay > 0 {
		timer := time.NewTimer(delay)
		defer timer.Stop()

		select {
		case <-ctx.Done():
			c.releaseLookup()
			return ctx.Err()
		case <-timer.C:
		}
	}

	c.nextLookup = time.Now().Add(c.lookupInterval)
	return nil
}

func (c *ITunesClient) releaseLookup() {
	c.lookupGate <- struct{}{}
}

// deferLookup must be called while lookupGate is held.
func (c *ITunesClient) deferLookup(delay time.Duration) {
	retryAt := time.Now().Add(delay)
	if retryAt.After(c.nextLookup) {
		c.nextLookup = retryAt
	}
}

func parseRetryAfter(value string, now time.Time) (time.Duration, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0, false
	}

	if seconds, err := strconv.ParseInt(value, 10, 64); err == nil {
		if seconds < 0 {
			return 0, false
		}
		return time.Duration(seconds) * time.Second, true
	}

	retryAt, err := http.ParseTime(value)
	if err != nil {
		return 0, false
	}

	delay := retryAt.Sub(now)
	if delay < 0 {
		delay = 0
	}
	return delay, true
}

func (c *ITunesClient) fetchArtworkImage(
	ctx context.Context,
	sourceURL string,
	advertisedWidth int,
) ([]byte, string, string, error) {
	candidateURLs := iTunesArtworkURLs(sourceURL, advertisedWidth)
	if len(candidateURLs) == 0 {
		return nil, "", "", fmt.Errorf(
			"iTunes artwork is smaller than %d pixels wide and its URL cannot be enlarged",
			iTunesArtworkMinimumWidth,
		)
	}

	var failures []error

	for _, candidateURL := range candidateURLs {
		imageBytes, contentType, resolvedURL, err :=
			c.fetchArtworkCandidate(ctx, candidateURL)
		if err == nil {
			return imageBytes, contentType, resolvedURL, nil
		}

		failures = append(failures, err)

		if ctx.Err() != nil {
			return nil, "", "", ctx.Err()
		}

		var rateLimitErr *artworkRateLimitError
		if errors.As(err, &rateLimitErr) {
			break
		}
	}

	return nil, "", "", fmt.Errorf(
		"fetch iTunes artwork: %w",
		errors.Join(failures...),
	)
}

func (c *ITunesClient) fetchArtworkCandidate(
	ctx context.Context,
	imageURL string,
) ([]byte, string, string, error) {
	resolvedURL, err := normalizeITunesArtworkURL(imageURL)
	if err != nil {
		return nil, "", "", err
	}

	for attempt := 0; ; attempt++ {
		request, err := http.NewRequestWithContext(
			ctx,
			http.MethodGet,
			resolvedURL,
			nil,
		)
		if err != nil {
			return nil, "", "", err
		}

		request.Header.Set("Accept", "image/*")
		request.Header.Set("User-Agent", c.userAgent)

		response, err := c.http.Do(request)
		if err != nil {
			return nil, "", "", err
		}

		if response.StatusCode == http.StatusTooManyRequests {
			retryAfter, hasRetryAfter := parseRetryAfter(
				response.Header.Get("Retry-After"),
				time.Now(),
			)
			response.Body.Close()

			if hasRetryAfter && attempt+1 < c.retryAttempts {
				if err := waitForRetry(ctx, retryAfter); err != nil {
					return nil, "", "", fmt.Errorf(
						"wait to retry iTunes artwork: %w",
						err,
					)
				}
				continue
			}

			return nil, "", "", &artworkRateLimitError{
				retryAfter:    retryAfter,
				hasRetryAfter: hasRetryAfter,
			}
		}

		defer response.Body.Close()

		if response.StatusCode < 200 || response.StatusCode >= 300 {
			return nil, "", "", fmt.Errorf(
				"artwork URL returned HTTP %d",
				response.StatusCode,
			)
		}

		imageBytes, err := readBoundedResponse(
			response.Body,
			app.ItunesArtworkMaxBytes,
		)
		if err != nil {
			return nil, "", "", err
		}
		if len(imageBytes) == 0 {
			return nil, "", "", errors.New("artwork response was empty")
		}

		contentType := http.DetectContentType(imageBytes)
		if !strings.HasPrefix(contentType, "image/") {
			return nil, "", "", fmt.Errorf(
				"artwork response has unsupported content type %q",
				contentType,
			)
		}

		config, _, err := image.DecodeConfig(bytes.NewReader(imageBytes))
		if err != nil {
			return nil, "", "", fmt.Errorf(
				"decode artwork dimensions: %w",
				err,
			)
		}
		if config.Width < iTunesArtworkMinimumWidth {
			return nil, "", "", fmt.Errorf(
				"artwork is %d pixels wide; minimum is %d",
				config.Width,
				iTunesArtworkMinimumWidth,
			)
		}

		return imageBytes, contentType, resolvedURL, nil
	}
}

func waitForRetry(ctx context.Context, delay time.Duration) error {
	if delay <= 0 {
		return ctx.Err()
	}

	timer := time.NewTimer(delay)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func buildITunesLookupURL(
	kind app.ITunesLookupKind,
	lookupID string,
) (string, error) {
	if kind != app.ITunesLookupAlbum && kind != app.ITunesLookupTrack {
		return "", fmt.Errorf("unsupported iTunes lookup kind %q", kind)
	}

	id, err := strconv.ParseInt(lookupID, 10, 64)
	if err != nil || id < 1 {
		return "", fmt.Errorf(
			"invalid iTunes %s ID %q",
			kind,
			lookupID,
		)
	}

	query := url.Values{}
	query.Set("id", lookupID)
	query.Set("media", "music")

	return app.ItunesLookupEndpoint + "?" + query.Encode(), nil
}

// Trust the artwork host returned by Apple's fixed HTTPS lookup endpoint rather
// than maintaining an allowlist of Apple's current CDN domains. Only absolute
// HTTP(S) URLs are accepted, and HTTP is upgraded before the artwork is fetched.
func normalizeITunesArtworkURL(value string) (string, error) {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" {
		return "", fmt.Errorf("invalid iTunes artwork URL %q", value)
	}

	switch parsed.Scheme {
	case "http":
		parsed.Scheme = "https"
	case "https":
	default:
		return "", fmt.Errorf(
			"unsupported iTunes artwork URL scheme %q",
			parsed.Scheme,
		)
	}

	return parsed.String(), nil
}

func readBoundedResponse(
	reader io.Reader,
	maxBytes int64,
) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return nil, err
	}

	if int64(len(data)) > maxBytes {
		return nil, fmt.Errorf(
			"response exceeded %d bytes",
			maxBytes,
		)
	}

	return data, nil
}
