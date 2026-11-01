package artwork

import (
	"encoding/json"
	"fmt"
	"net/url"
	"path"
	"regexp"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
)

var (
	artworkURLKeyPattern = regexp.MustCompile(
		`^artworkUrl([0-9]+)$`,
	)
	fixedArtworkFilenamePattern = regexp.MustCompile(
		`(?i)^([0-9]+)x([0-9]+)((bb|sr)?(-[^.]+)?)\.(jpe?g|png|webp)$`,
	)
)

// selectITunesArtworkURL selects the best artwork URL from an iTunes Lookup
// response. The boolean is false when the response is valid but has no artwork.
func selectITunesArtworkURL(data []byte) (string, int, bool, error) {
	var payload struct {
		Results []map[string]any `json:"results"`
	}

	if err := json.Unmarshal(data, &payload); err != nil {
		return "", 0, false, fmt.Errorf(
			"decode iTunes lookup response: %w",
			err,
		)
	}

	type candidate struct {
		resultRank int
		imageSize  int
		imageURL   string
	}

	var selected *candidate

	for _, result := range payload.Results {
		imageURL, imageSize := largestArtworkURL(result)
		if imageURL == "" {
			continue
		}

		rank := 2
		if wrapperType, _ := result["wrapperType"].(string); wrapperType == "collection" {
			rank = 0
		} else if _, ok := result["collectionId"]; ok {
			rank = 1
		}

		current := candidate{
			resultRank: rank,
			imageSize:  imageSize,
			imageURL:   imageURL,
		}

		if selected == nil ||
			current.resultRank < selected.resultRank ||
			(current.resultRank == selected.resultRank &&
				current.imageSize > selected.imageSize) {
			selected = &current
		}
	}

	if selected == nil {
		return "", 0, false, nil
	}

	return selected.imageURL, selected.imageSize, true, nil
}

func largestArtworkURL(result map[string]any) (string, int) {
	largestSize := -1
	largestURL := ""

	for key, rawValue := range result {
		matches := artworkURLKeyPattern.FindStringSubmatch(key)
		if matches == nil {
			continue
		}

		imageURL, ok := rawValue.(string)
		if !ok || !isHTTPURL(imageURL) {
			continue
		}

		size, err := strconv.Atoi(matches[1])
		if err != nil {
			continue
		}

		if size > largestSize {
			largestSize = size
			largestURL = imageURL
		}
	}

	return largestURL, largestSize
}

// iTunesArtworkURLs returns preferred enlarged artwork URLs followed by the
// original URL when Apple advertised it as large enough to use directly.
func iTunesArtworkURLs(imageURL string, advertisedWidth int) []string {
	if !isHTTPURL(imageURL) {
		return nil
	}

	urls := make([]string, 0, 4)
	seen := make(map[string]struct{}, 4)

	for _, width := range [...]int{1200, 600, app.ArtworkMinimumWidth} {
		enlarged := highResolutionITunesArtworkURL(imageURL, width)
		if enlarged == imageURL {
			continue
		}
		if _, exists := seen[enlarged]; exists {
			continue
		}

		urls = append(urls, enlarged)
		seen[enlarged] = struct{}{}
	}

	if advertisedWidth >= app.ArtworkMinimumWidth {
		if _, exists := seen[imageURL]; !exists {
			urls = append(urls, imageURL)
		}
	}

	return urls
}

func highResolutionITunesArtworkURL(
	imageURL string,
	size int,
) string {
	if size < 1 || !isHTTPURL(imageURL) {
		return imageURL
	}

	parsed, err := url.Parse(imageURL)
	if err != nil {
		return imageURL
	}

	sizeText := strconv.Itoa(size)
	rewrittenPath := strings.ReplaceAll(parsed.Path, "{w}", sizeText)
	rewrittenPath = strings.ReplaceAll(rewrittenPath, "{h}", sizeText)

	if rewrittenPath != parsed.Path {
		parsed.Path = rewrittenPath
		parsed.RawPath = ""
		return parsed.String()
	}

	directory, filename := path.Split(parsed.Path)
	matches := fixedArtworkFilenamePattern.FindStringSubmatch(filename)
	if matches == nil {
		return imageURL
	}

	suffix := matches[3]
	extension := matches[6]

	parsed.Path = directory +
		sizeText + "x" + sizeText + suffix + "." + extension
	parsed.RawPath = ""

	return parsed.String()
}

func isHTTPURL(value string) bool {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Host == "" {
		return false
	}

	return parsed.Scheme == "http" || parsed.Scheme == "https"
}
