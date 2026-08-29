package artwork

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type rateLimitedHTTPDoer struct {
	calls int
}

func (doer *rateLimitedHTTPDoer) Do(*http.Request) (*http.Response, error) {
	doer.calls++
	return &http.Response{
		StatusCode: http.StatusTooManyRequests,
		Header: http.Header{
			"Retry-After": []string{"0"},
		},
		Body: io.NopCloser(strings.NewReader("")),
	}, nil
}

func TestITunesClientConfiguredRetryAttempts(t *testing.T) {
	httpClient := new(rateLimitedHTTPDoer)
	client := NewITunesClient(httpClient, ITunesClientConfig{
		RequestTimeout: time.Second,
		LookupInterval: 0,
		RetryAttempts:  3,
	})

	_, _, err := client.fetchLookup(context.Background(), "https://example.test")
	if err == nil {
		t.Fatal("fetch lookup succeeded, want rate-limit error")
	}
	if got, want := httpClient.calls, 3; got != want {
		t.Errorf("request count = %d, want %d", got, want)
	}
}

func TestITunesClientConfiguredRequestTimeout(t *testing.T) {
	client := NewITunesClient(nil, ITunesClientConfig{
		RequestTimeout: 7 * time.Second,
		LookupInterval: 0,
		RetryAttempts:  1,
	})

	httpClient, ok := client.http.(*http.Client)
	if !ok {
		t.Fatalf("HTTP client type = %T, want *http.Client", client.http)
	}
	if got, want := httpClient.Timeout, 7*time.Second; got != want {
		t.Errorf("request timeout = %s, want %s", got, want)
	}
}
