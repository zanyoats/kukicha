package query

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/home_library_state.sql
var homeLibraryStateSQL string

//go:embed sql/home_artists.sql
var homeArtistsSQL string

//go:embed sql/home_genres.sql
var homeGenresSQL string

//go:embed sql/home_styles.sql
var homeStylesSQL string

// HomeItem is an artist, genre, or style displayed on the home page.
type HomeItem struct {
	ID   int64
	Name string
}

// HomeData contains the library categories displayed on the home page.
type HomeData struct {
	LibraryGenerated bool
	LibraryHasTracks bool
	InitialSyncState string
	Artists          []HomeItem
	Genres           []HomeItem
	Styles           []HomeItem
}

// Home loads the artists, genres, and styles associated with library albums.
func Home(ctx context.Context, db *sql.DB) (HomeData, error) {
	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return HomeData{}, fmt.Errorf("begin home query: %w", err)
	}
	defer tx.Rollback()

	result := HomeData{}
	if err := tx.QueryRowContext(ctx, homeLibraryStateSQL,
		sql.Named("generated_at_key", app.LibraryGeneratedAt),
	).Scan(&result.LibraryGenerated, &result.LibraryHasTracks, &result.InitialSyncState); err != nil {
		return HomeData{}, fmt.Errorf("query home library state: %w", err)
	}

	result.Artists, err = queryHomeItems(ctx, tx, homeArtistsSQL, "artists")
	if err != nil {
		return HomeData{}, err
	}

	result.Genres, err = queryHomeItems(ctx, tx, homeGenresSQL, "genres")
	if err != nil {
		return HomeData{}, err
	}

	result.Styles, err = queryHomeItems(ctx, tx, homeStylesSQL, "styles")
	if err != nil {
		return HomeData{}, err
	}

	if err := tx.Commit(); err != nil {
		return HomeData{}, fmt.Errorf("commit home query: %w", err)
	}

	return result, nil
}

func queryHomeItems(
	ctx context.Context,
	tx *sql.Tx,
	query string,
	kind string,
) ([]HomeItem, error) {
	rows, err := tx.QueryContext(ctx, query)
	if err != nil {
		return nil, fmt.Errorf("query home %s: %w", kind, err)
	}
	defer rows.Close()

	items := make([]HomeItem, 0)

	for rows.Next() {
		var item HomeItem
		if err := rows.Scan(&item.ID, &item.Name); err != nil {
			return nil, fmt.Errorf("scan home %s: %w", kind, err)
		}

		items = append(items, item)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate home %s: %w", kind, err)
	}

	return items, nil
}
