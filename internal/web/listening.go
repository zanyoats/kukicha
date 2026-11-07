package web

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"slices"
	"strconv"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type listeningStat struct{ Value, PreviousValue, Label, Change, Direction string }
type listeningView struct {
	Years                                                    []int
	YearToDate                                               bool
	GenresURL                                                string
	Genres                                                   []query.ListeningGenre
	MonthLabels                                              []string
	HoursURL                                                 string
	Hours                                                    []listeningHour
	Timezone                                                 string
	URLs                                                     URLs
	EyeBrow, Heading, SummaryURL, ChartURL, ChartData, Error string
	Year, PreviousYear                                       int
	Stats                                                    []listeningStat
	Months                                                   []listeningMonth
}
type listeningMonth struct{ Name, Current, Previous string }

func formatListeningTime(seconds int64) string {
	return fmt.Sprintf("%s days, %d hours", formatNumber(seconds/86400), seconds/3600%24)
}

func listeningChange(current, previous float64) (string, string) {
	if previous == 0 {
		if current == 0 {
			return "0%", "unchanged"
		}
		return "No prior baseline", "unchanged"
	}
	change := (current - previous) / previous * 100
	if math.Abs(change) < 0.05 {
		return "0%", "unchanged"
	}
	if change > 0 {
		return fmt.Sprintf("+%.1f%%", change), "increase"
	}
	return fmt.Sprintf("%.1f%%", change), "decrease"
}

func (c *webRouter) showListeningData(w http.ResponseWriter, r *http.Request) {
	at := time.Now()
	w.Header().Set("Cache-Control", "no-store")
	years, err := query.ListeningYears(r.Context(), c.db, at)
	if err != nil {
		slog.ErrorContext(r.Context(), "query listening years", "error", err)
		http.Error(w, "Unable to load listening years.", http.StatusInternalServerError)
		return
	}
	if !slices.Contains(years, at.Year()) {
		years = append([]int{at.Year()}, years...)
	}
	year := at.Year()
	if value := r.URL.Query().Get("year"); value != "" {
		year, err = strconv.Atoi(value)
		if err != nil || !slices.Contains(years, year) {
			http.Error(w, "Invalid listening year.", http.StatusBadRequest)
			return
		}
	}
	suffix := "?at=" + strconv.FormatInt(at.UnixMilli(), 10) + "&year=" + strconv.Itoa(year)
	renderPage(w, c.renderer, "listening.gohtml", listeningView{
		URLs: c.urls, EyeBrow: "Your library", Heading: "Listening Data",
		Year: year, PreviousYear: year - 1, Years: years, YearToDate: year == at.Year(),
		Timezone:   at.Format("MST (UTC-07:00)"),
		SummaryURL: c.urls.ListeningSummary + suffix, ChartURL: c.urls.ListeningChart + suffix,
		HoursURL:  c.urls.ListeningHours + suffix,
		GenresURL: c.urls.ListeningGenres + suffix,
	})
}

// All fragments share the page's selected year and cutoff. Completed years end
// exactly at the next January 1, so the final millisecond is included.
func listeningCutoff(r *http.Request) (int, time.Time, error) {
	ms, err := strconv.ParseInt(r.URL.Query().Get("at"), 10, 64)
	if err != nil || ms <= 0 {
		return 0, time.Time{}, fmt.Errorf("invalid listening cutoff")
	}
	at := time.UnixMilli(ms).In(time.Local)
	year, err := strconv.Atoi(r.URL.Query().Get("year"))
	if err != nil || year < 1 || year > at.Year() || year > time.Now().Year() {
		return 0, time.Time{}, fmt.Errorf("invalid listening year")
	}
	if year < at.Year() {
		at = time.Date(year+1, 1, 1, 0, 0, 0, 0, time.Local)
	}
	return year, at, nil
}

func (c *webRouter) showListeningSummary(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	year, at, err := listeningCutoff(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	view := listeningView{PreviousYear: year - 1, SummaryURL: r.URL.RequestURI()}
	data, err := query.ListeningSummaryAt(r.Context(), c.db, year, at)
	if err != nil {
		slog.ErrorContext(r.Context(), "query listening summary", "error", err)
		view.Error = "Unable to load listening statistics."
	} else {
		daily := float64(data.Count) / float64(data.Days)
		previousDaily := float64(data.PreviousCount) / float64(data.PreviousDays)
		for _, stat := range []struct {
			value, previousValue, label string
			current, previous           float64
		}{
			{formatNumber(data.Count), formatNumber(data.PreviousCount), "scrobbles", float64(data.Count), float64(data.PreviousCount)},
			{formatListeningTime(data.Seconds), formatListeningTime(data.PreviousSeconds), "listening", float64(data.Seconds), float64(data.PreviousSeconds)},
			{formatNumber(int64(math.Round(daily))), formatNumber(int64(math.Round(previousDaily))), "scrobbles / day", daily, previousDaily},
		} {
			change, direction := listeningChange(stat.current, stat.previous)
			view.Stats = append(view.Stats, listeningStat{Value: stat.value, PreviousValue: stat.previousValue, Label: stat.label, Change: change, Direction: direction})
		}
	}
	renderPage(w, c.renderer, "listening_summary.gohtml", view)
}

func (c *webRouter) showListeningChart(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	year, at, err := listeningCutoff(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	view := listeningView{Year: year, YearToDate: at.Year() == year, PreviousYear: year - 1, ChartURL: r.URL.RequestURI()}
	data, err := query.ListeningMonthsAt(r.Context(), c.db, year, at)
	if err != nil {
		slog.ErrorContext(r.Context(), "query listening months", "error", err)
		view.Error = "Unable to load monthly scrobbles."
	} else {
		encoded, _ := json.Marshal(data)
		view.ChartData = string(encoded)
		for i := range 12 {
			current := "—"
			if data.Current[i] != nil {
				current = formatNumber(*data.Current[i])
			}
			view.Months = append(view.Months, listeningMonth{time.Month(i + 1).String()[:3], current, formatNumber(data.Previous[i])})
		}
	}
	renderPage(w, c.renderer, "listening_chart.gohtml", view)
}

type listeningHour struct {
	Label string
	Count int64
}

func (c *webRouter) showListeningHours(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	year, at, err := listeningCutoff(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	view := listeningView{Year: year, YearToDate: at.Year() == year, HoursURL: r.URL.RequestURI()}
	counts, err := query.ListeningHoursAt(r.Context(), c.db, year, at)
	if err != nil {
		slog.ErrorContext(r.Context(), "query listening hours", "error", err)
		view.Error = "Unable to load hourly scrobbles."
	} else {
		encoded, _ := json.Marshal(counts)
		view.ChartData = string(encoded)
		for hour, count := range counts {
			view.Hours = append(view.Hours, listeningHour{fmt.Sprintf("%02d:00–%02d:00", hour, (hour+1)%24), count})
		}
	}
	renderPage(w, c.renderer, "listening_hours.gohtml", view)
}

func (c *webRouter) showListeningGenres(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	year, at, err := listeningCutoff(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	view := listeningView{Year: year, YearToDate: at.Year() == year, GenresURL: r.URL.RequestURI()}
	genres, err := query.ListeningGenresAt(r.Context(), c.db, year, at)
	if err != nil {
		slog.ErrorContext(r.Context(), "query listening genres", "error", err)
		view.Error = "Unable to load plays by genre."
	} else {
		view.Genres = genres
		encoded, _ := json.Marshal(struct {
			Genres      []query.ListeningGenre `json:"genres"`
			UnknownItem string                 `json:"unknownItem"`
		}{Genres: genres, UnknownItem: app.UnknownItem})
		view.ChartData = string(encoded)
		for month := time.January; month <= time.December; month++ {
			view.MonthLabels = append(view.MonthLabels, month.String()[:3])
		}
	}
	renderPage(w, c.renderer, "listening_genres.gohtml", view)
}
