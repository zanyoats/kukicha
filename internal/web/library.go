package web

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type libraryViewModel struct {
	EyeBrow     string
	URLs        URLs
	Heading     string
	Albums      []albumCard
	SortOptions []albumSortOption
	PreviousURL string
	NextURL     string
	FirstURL    string
}

type albumSortOption struct {
	Label   string
	URL     string
	Current bool
}

func libraryFilterValues(filters query.AlbumFilters) url.Values {
	values := url.Values{}
	for key, id := range map[string]int64{
		"artist_id": filters.ArtistID,
		"genre_id":  filters.GenreID,
		"style_id":  filters.StyleID,
	} {
		if id != 0 {
			values.Set(key, strconv.FormatInt(id, 10))
		}
	}
	return values
}

func libraryPageURL(libraryURL string, filters query.AlbumFilters, sort app.AlbumSort, offset int) string {
	values := libraryFilterValues(filters)
	values.Set("sort", string(sort))
	if offset > 0 {
		values.Set("offset", strconv.Itoa(offset))
	}
	return libraryURL + "?" + values.Encode()
}

func librarySortOptions(libraryURL string, filters query.AlbumFilters, current app.AlbumSort) []albumSortOption {
	values := libraryFilterValues(filters)
	options := make([]albumSortOption, 0, 3)
	for _, option := range []struct {
		sort  app.AlbumSort
		label string
	}{
		{app.AlbumSortRecentlyAdded, "Recently added"},
		{app.AlbumSortAlbum, "Album"},
		{app.AlbumSortArtistAlbum, "Artist, album"},
	} {
		values.Set("sort", string(option.sort))
		options = append(options, albumSortOption{
			Label:   option.label,
			URL:     libraryURL + "?" + values.Encode(),
			Current: option.sort == current,
		})
	}
	return options
}

type albumCard struct {
	Title        string
	Artists      []albumCategoryLink
	SmallArtwork artworkViewModel
	LargeArtwork artworkViewModel
	AlbumURL     string
}

type artworkViewModel struct {
	URL    string
	Width  int
	Height int
}

type albumViewModel struct {
	EyeBrow       string
	Heading       string
	URLs          URLs
	Album         *query.Album
	SmallArtwork  artworkViewModel
	LargeArtwork  artworkViewModel
	AlbumPageData string
	Genres        []albumCategoryLink
	Styles        []albumCategoryLink
}

type albumCategoryLink struct {
	Name string
	URL  string
}

type playerAlbumPageData struct {
	Tracks []playerAlbumPageTrack `json:"tracks"`
}

type playerAlbumPageTrack struct {
	ID              string   `json:"id"`
	AudioURL        string   `json:"audioURL"`
	Title           string   `json:"title"`
	Artists         string   `json:"artists"`
	Album           string   `json:"album"`
	AlbumURL        string   `json:"albumURL"`
	AlbumArtists    []string `json:"albumArtists"`
	ArtworkURL      string   `json:"artworkURL"`
	ArtworkWidth    int      `json:"artworkWidth"`
	ArtworkHeight   int      `json:"artworkHeight"`
	DurationSeconds int64    `json:"durationSeconds"`
}

func (c *appRoutes) showLibrary(w http.ResponseWriter, r *http.Request) {
	filters, err := parseAlbumFilters(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	switch {
	case filters.AlbumID != 0: // show album page
		album, err := query.AlbumByFilters(r.Context(), c.db, filters)
		if errors.Is(err, query.ErrAlbumNotFound) {
			http.Error(w, "Album not found", http.StatusNotFound)
			return
		} else if err != nil {
			internalServerError(
				w,
				"unable to load album",
				"album_id", filters.AlbumID,
				"error", err,
			)
			return
		}

		smallArtwork := newArtworkViewModel(c.urls, album.ID, query.AlbumArtwork64, album.SmallArtwork)
		largeArtwork := newArtworkViewModel(c.urls, album.ID, query.AlbumArtwork256, album.LargeArtwork)
		albumPageDataJSON, err := json.Marshal(
			newAlbumPageData(album, c.urls, smallArtwork),
		)
		if err != nil {
			internalServerError(
				w,
				"unable to encode player queue",
				"album_id", filters.AlbumID,
				"error", err,
			)
			return
		}

		renderPage(w, c.renderer, "album.gohtml", albumViewModel{
			EyeBrow:       "Collection",
			Heading:       album.Title,
			URLs:          c.urls,
			Album:         &album,
			SmallArtwork:  smallArtwork,
			LargeArtwork:  largeArtwork,
			AlbumPageData: string(albumPageDataJSON),
			Genres: albumCategoryLinks(album.Genres, func(id int64) string {
				return c.urls.GenreURL(id)
			}),
			Styles: albumCategoryLinks(album.Styles, func(id int64) string {
				return c.urls.StyleURL(id)
			}),
		})

	default: // show library (album collection) page
		sort, err := parseAlbumSort(r, c.defaultAlbumSort)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		offset, err := parseAlbumOffset(r)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		filterNames, err := query.NamesForAlbumFilters(r.Context(), c.db, filters)
		if err != nil {
			internalServerError(w, "unable to load album filter names", "error", err)
			return
		}

		heading, err := libraryHeading(filters, filterNames)
		if err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}

		page, err := query.Albums(r.Context(), c.db, filters, sort, c.perPage, offset)
		if err != nil {
			internalServerError(w, "unable to load albums", "error", err)
			return
		}

		albumCards := make([]albumCard, 0, len(page.Albums))

		for _, album := range page.Albums {
			albumCards = append(albumCards, albumCard{
				Title:        album.Title,
				Artists:      albumArtistLinks(album.Artists, c.urls),
				SmallArtwork: newArtworkViewModel(c.urls, album.ID, query.AlbumArtwork64, album.SmallArtwork),
				LargeArtwork: newArtworkViewModel(c.urls, album.ID, query.AlbumArtwork256, album.LargeArtwork),
				AlbumURL:     c.urls.AlbumURL(album.ID),
			})
		}

		model := libraryViewModel{
			EyeBrow:     "Collection",
			URLs:        c.urls,
			Heading:     heading,
			Albums:      albumCards,
			SortOptions: librarySortOptions(c.urls.Library, filters, sort),
		}
		if offset > 0 {
			model.PreviousURL = libraryPageURL(c.urls.Library, filters, sort, max(0, offset-c.perPage))
		}
		if page.HasNext {
			model.NextURL = libraryPageURL(c.urls.Library, filters, sort, offset+c.perPage)
		}
		if offset > 0 && len(page.Albums) == 0 {
			model.FirstURL = libraryPageURL(c.urls.Library, filters, sort, 0)
		}
		renderPage(w, c.renderer, "library.gohtml", model)
	}
}

func libraryHeading(filters query.AlbumFilters, names query.AlbumFilterNames) (string, error) {
	var parts []string
	for _, filter := range []struct {
		id       int64
		name     *string
		notFound string
	}{
		{filters.ArtistID, names.Artist, "Artist not found"},
		{filters.GenreID, names.Genre, "Genre not found"},
		{filters.StyleID, names.Style, "Style not found"},
	} {
		if filter.id == 0 {
			continue
		}
		if filter.name == nil {
			return "", errors.New(filter.notFound)
		}
		parts = append(parts, *filter.name)
	}
	if len(parts) == 0 {
		return "Albums", nil
	}
	return strings.Join(parts, " · "), nil
}

func newAlbumPageData(
	album query.Album,
	urls URLs,
	artwork artworkViewModel,
) playerAlbumPageData {
	artistNames := make([]string, 0, len(album.Artists))
	for _, artist := range album.Artists {
		artistNames = append(artistNames, artist.Name)
	}
	tracks := make([]playerAlbumPageTrack, 0, len(album.Tracks))
	for _, track := range album.Tracks {
		tracks = append(tracks, playerAlbumPageTrack{
			ID:              strconv.FormatInt(track.ID, 10),
			AudioURL:        urls.TrackAudioURL(track.ID),
			Title:           track.Title,
			Artists:         strings.Join(track.Artists, " "),
			Album:           album.Title,
			AlbumURL:        urls.AlbumURL(album.ID),
			AlbumArtists:    artistNames,
			ArtworkURL:      artwork.URL,
			ArtworkWidth:    artwork.Width,
			ArtworkHeight:   artwork.Height,
			DurationSeconds: track.DurationSeconds,
		})
	}

	return playerAlbumPageData{Tracks: tracks}
}

func newArtworkViewModel(urls URLs, albumID int64, size query.AlbumArtworkSize, dimensions query.ArtworkDimensions) artworkViewModel {
	return artworkViewModel{
		URL:    urls.AlbumArtworkURL(albumID, size),
		Width:  dimensions.Width,
		Height: dimensions.Height,
	}
}

func albumArtistLinks(artists []query.AlbumArtist, urls URLs) []albumCategoryLink {
	links := make([]albumCategoryLink, 0, len(artists))
	for _, artist := range artists {
		links = append(links, albumCategoryLink{Name: artist.Name, URL: urls.ArtistURL(artist.ID)})
	}
	return links
}

func albumCategoryLinks(items []query.AlbumCategory, itemURL func(int64) string) []albumCategoryLink {
	links := make([]albumCategoryLink, 0, len(items))
	for _, item := range items {
		links = append(links, albumCategoryLink{Name: item.Name, URL: itemURL(item.ID)})
	}
	return links
}
