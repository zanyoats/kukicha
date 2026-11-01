package command

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var (
	ErrInvalidRadioRequest    = errors.New("invalid radio request")
	ErrRadioNotFound          = errors.New("radio not found")
	ErrRadioEmpty             = errors.New("radio has no tracks")
	ErrRadioMembershipChanged = errors.New("radio membership changed")
)

//go:embed sql/radio_track.sql
var radioTrackSQL string

// RadioState lives in each player's sessionStorage. Integers travel as decimal
// strings so JavaScript cannot round int64 shuffle parameters or entity IDs.
type RadioState struct {
	MembershipVersion string `json:"membership_version"`
	TrackCount        int64  `json:"track_count,string"`
	Prime             int64  `json:"prime,string"`
	Multiplier        int64  `json:"multiplier,string"`
	Offset            int64  `json:"offset,string"`
	CurrentPosition   int64  `json:"current_position,string"`
}

type RadioRequest struct {
	Kind     string `json:"kind"`
	EntityID int64  `json:"entity_id,string"`
	// There are 3 actions:
	// 1. start -> creates a fresh shuffle (i.e. radio)
	// 2. track -> resolves the track from RadioState.CurrentPosition
	// 3. cycle -> updates the shuffle with fresh parameters (i.e. new radio)
	Action string      `json:"action"`
	State  *RadioState `json:"state,omitempty"`
}

type RadioSelection struct {
	State      RadioState
	Name       string
	Track      query.AlbumTrack
	AlbumID    int64
	AlbumTitle string
}

type radioSource struct{ members, memberID, entities, name string }

func sourceForRadio(kind string) (radioSource, bool) {
	switch kind {
	case "artist":
		return radioSource{"artists_radios", "artist_id", "library_artist_ids", "artist"}, true
	case "genre":
		return radioSource{"genres_radios", "genre_id", "taxonomy_genres", "genre"}, true
	case "style":
		return radioSource{"styles_radios", "style_id", "taxonomy_styles", "style"}, true
	default:
		return radioSource{}, false
	}
}

// SelectRadioTrack resolves the current radio selection based on RadioRequest.
// It does not store any state, the client maintains the cursor, it only resolves the track.
func SelectRadioTrack(ctx context.Context, db *sql.DB, request RadioRequest) (RadioSelection, error) {
	source, valid := sourceForRadio(request.Kind)
	if !valid || request.EntityID <= 0 {
		return RadioSelection{}, ErrInvalidRadioRequest
	}
	switch request.Action {
	case "start":
		if request.State != nil {
			return RadioSelection{}, ErrInvalidRadioRequest
		}
	case "track", "cycle":
		if request.State == nil {
			return RadioSelection{}, ErrInvalidRadioRequest
		}
	default:
		return RadioSelection{}, ErrInvalidRadioRequest
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return RadioSelection{}, err
	}
	defer tx.Rollback()
	// This first read establishes the snapshot also used for count and track lookup.
	version, err := query.AppMetadata(ctx, tx, app.RadioMembershipVersion)
	if err != nil && !errors.Is(err, query.ErrAppMetadataNotFound) {
		return RadioSelection{}, err
	}
	if request.State != nil && (version == "" || version != request.State.MembershipVersion) {
		return RadioSelection{}, ErrRadioMembershipChanged
	}
	if version == "" {
		return RadioSelection{}, ErrRadioEmpty
	}
	var selection RadioSelection
	err = tx.QueryRowContext(
		ctx,
		fmt.Sprintf("SELECT %s FROM %s WHERE id = ?", source.name, source.entities),
		request.EntityID,
	).Scan(&selection.Name)
	if errors.Is(err, sql.ErrNoRows) {
		return RadioSelection{}, ErrRadioNotFound
	}
	if err != nil {
		return RadioSelection{}, fmt.Errorf("load radio name: %w", err)
	}
	// Dense positions let the primary key answer this without counting every row.
	var count int64
	err = tx.QueryRowContext(
		ctx,
		fmt.Sprintf("SELECT COALESCE(MAX(position) + 1, 0) FROM %s WHERE %s = ?",
			source.members, source.memberID,
		),
		request.EntityID,
	).Scan(&count)
	if err != nil {
		return RadioSelection{}, fmt.Errorf("count radio tracks: %w", err)
	}
	if count == 0 {
		return RadioSelection{}, ErrRadioEmpty
	}
	var shuffle radioShuffle
	var position int64
	if request.State != nil {
		state := request.State
		// Do not trust client parameters: a far larger prime could cause excessive
		// cycle walking. Require exactly the prime our generator uses for this count.
		prime, err := radioPrime(count)
		if err != nil {
			return RadioSelection{}, err
		}
		if state.TrackCount != count ||
			state.Prime != prime ||
			state.Multiplier < 1 ||
			state.Multiplier >= prime ||
			state.Offset < 0 ||
			state.Offset >= prime ||
			state.CurrentPosition < 0 ||
			state.CurrentPosition >= count {
			return RadioSelection{}, ErrInvalidRadioRequest
		}
		shuffle = radioShuffle{count, prime, state.Multiplier, state.Offset}
		position = state.CurrentPosition
	}
	if request.Action != "track" {
		old := shuffle
		for {
			shuffle, err = newRadioShuffle(count)
			if err != nil {
				return RadioSelection{}, err
			}
			if shuffle != old {
				break
			}
		}
		position = 0
	}
	selection.State = RadioState{
		version, count, shuffle.prime, shuffle.multiplier, shuffle.offset, position,
	}
	var artists string
	err = tx.QueryRowContext(ctx,
		fmt.Sprintf(radioTrackSQL, source.members, source.memberID),
		sql.Named("entity_id", request.EntityID),
		sql.Named("position", shuffle.index(position)),
	).Scan(
		&selection.Track.ID, &selection.Track.RootConfigID, &selection.Track.Title, &artists,
		&selection.Track.DurationSeconds, &selection.Track.DiscNumber, &selection.Track.TrackNumber,
		&selection.AlbumID, &selection.AlbumTitle)
	if err != nil {
		return RadioSelection{}, fmt.Errorf("load radio track: %w", err)
	}
	if err := json.Unmarshal([]byte(artists), &selection.Track.Artists); err != nil {
		return RadioSelection{}, fmt.Errorf("decode radio artists: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return RadioSelection{}, err
	}
	return selection, nil
}
