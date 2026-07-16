package command

import (
	"context"
	"database/sql"
	"fmt"
	"log"
)

type migration struct {
	version int
	comment string
	sql     string
}

var migrations = []migration{
	{
		version: 1,
		comment: "creating initial data model",
		sql: `
			CREATE TABLE app_metadata (
				key   INTEGER PRIMARY KEY,
				value TEXT    NOT NULL
			);

			CREATE TABLE taxonomy_genres (
				genre TEXT PRIMARY KEY COLLATE NOCASE
			);

			CREATE TABLE taxonomy_styles (
				style        TEXT PRIMARY KEY COLLATE NOCASE,
				parent_genre TEXT NOT NULL    COLLATE NOCASE,

				FOREIGN KEY (parent_genre)
				  REFERENCES taxonomy_genres (genre)
				  ON DELETE CASCADE
			);

			CREATE TABLE taxonomy (
				id     INTEGER PRIMARY KEY,
				kind   TEXT    NOT NULL   ,
				name   TEXT    NOT NULL   , -- canonical
				parent TEXT    NOT NULL   ,
				source TEXT    NOT NULL   ,
				alias  TEXT    NOT NULL   ,
				status TEXT    NOT NULL   ,
				count  INTEGER            ,

				CHECK(
					    kind in ('genre', 'style')
					and source in ('discogs_20260301_masters', 'mb_genres_v0')
				)
			);

			CREATE TABLE album_artist_split_mappings (
				album_key      TEXT PRIMARY KEY,
				mapped_artists TEXT NOT NULL   , -- JSON array

				CHECK(json_valid(mapped_artists))
			) WITHOUT ROWID;

			CREATE TABLE library_roots (
				id        INTEGER PRIMARY KEY AUTOINCREMENT,
				pos       INTEGER NOT NULL                 ,
				path      TEXT    NOT NULL                 ,

				UNIQUE (pos)                               ,
				UNIQUE (path)
			);

			CREATE TABLE library_albums (
				id                    INTEGER PRIMARY KEY AUTOINCREMENT,
				album_key             TEXT    NOT NULL                 ,
				album                 TEXT    NOT NULL                 ,
				format                TEXT    NOT NULL                 ,
				track_count           INTEGER                          ,
				disc_count            INTEGER                          ,
				release_date          TEXT                             ,
				original_release_date TEXT                             ,
				recorded_date         TEXT                             ,
				release_year          INTEGER                          ,
				original_release_year INTEGER                          ,
				is_compilation        INTEGER                          ,

				UNIQUE (album_key)
			);

			CREATE TABLE library_album_artists (
				id         INTEGER PRIMARY KEY AUTOINCREMENT,
				album_id   INTEGER NOT NULL                 ,
				artist_key TEXT    NOT NULL                 ,
				artist     TEXT    NOT NULL                 ,

				UNIQUE (album_id, artist_key)               ,
				FOREIGN KEY (album_id)
				  REFERENCES library_albums (id)
				  ON DELETE CASCADE
			);

			CREATE TABLE library_track_sidecars (
				id                   INTEGER PRIMARY KEY AUTOINCREMENT,
				track_id             INTEGER NOT NULL                 ,
				stem                 TEXT    NOT NULL                 ,
				ext                  TEXT    NOT NULL                 ,
				path                 TEXT    NOT NULL                 ,
				file_size            INTEGER NOT NULL                 ,
				file_mtime_unix_nano INTEGER NOT NULL                 ,

				FOREIGN KEY (track_id)
				  REFERENCES library_tracks (id)
				  ON DELETE CASCADE
			);

			CREATE TABLE library_track_embedded_artwork (
				id                   INTEGER PRIMARY KEY AUTOINCREMENT,
				track_id             INTEGER NOT NULL                 ,
				picture_type         TEXT    NOT NULL                 ,
				artwork_content_type TEXT                             ,
				artwork_content_size INTEGER NOT NULL                 ,
				artwork_bytes        BLOB    NOT NULL                 ,

				FOREIGN KEY (track_id)
				  REFERENCES library_tracks (id)
				  ON DELETE CASCADE
			);

			CREATE TABLE library_track_genres (
				id       INTEGER PRIMARY KEY AUTOINCREMENT,
				track_id INTEGER NOT NULL                 ,
				genre    TEXT    NOT NULL                 ,

				UNIQUE (track_id, genre)                  ,
				FOREIGN KEY (track_id)
				  REFERENCES library_tracks (id)
				  ON DELETE CASCADE                       ,
				FOREIGN KEY (genre)
				  REFERENCES taxonomy_genres (genre)
				  ON DELETE CASCADE
			);

			CREATE TABLE library_track_styles (
				id       INTEGER PRIMARY KEY AUTOINCREMENT,
				track_id INTEGER NOT NULL                 ,
				style    TEXT    NOT NULL                 ,

				UNIQUE (track_id, style)                  ,
				FOREIGN KEY (track_id)
				  REFERENCES library_tracks (id)
				  ON DELETE CASCADE                       ,
				FOREIGN KEY (style)
				  REFERENCES taxonomy_styles (style)
				  ON DELETE CASCADE
			);

			CREATE TABLE library_tracks (
				id                   INTEGER PRIMARY KEY AUTOINCREMENT,
				root_id              INTEGER NOT NULL                 ,
				album_id             INTEGER NOT NULL                 ,
				ext                  TEXT    NOT NULL                 ,
				path                 TEXT    NOT NULL                 ,
				file_type            INTEGER NOT NULL                 ,
				file_size            INTEGER NOT NULL                 ,
				file_mtime_unix_nano INTEGER NOT NULL                 ,
				track_metadata       TEXT    NOT NULL                 ,
				scan_error           TEXT    NOT NULL                 ,
				title                TEXT    NOT NULL                 ,
				artists              TEXT    NOT NULL                 , -- JSON Array
				work                 TEXT                             ,
				grouping             TEXT                             ,
				movement_name        TEXT                             ,
				movement_number      INTEGER                          ,
				movement_count       INTEGER                          ,
				show_movement        INTEGER                          ,
				track_number         INTEGER                          ,
				disc_number          INTEGER                          ,

				UNIQUE (root_id, path)                                ,
				CHECK (
						    json_valid(artists)
						AND json_type(artists) = 'array'
				)                                                     ,
				FOREIGN KEY (root_id)
				  REFERENCES library_roots (id)
				  ON DELETE CASCADE                                   ,
				FOREIGN KEY (album_id)
				  REFERENCES library_albums (id)
				  ON DELETE CASCADE
			);
		`,
	},
}

func Migrate(ctx context.Context, writerDb *sql.DB) error {
	conn, err := writerDb.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()

	var version int
	if err := conn.QueryRowContext(ctx, `PRAGMA user_version`).Scan(&version); err != nil {
		return err
	}

	for _, migration := range migrations {
		if migration.version <= version {
			continue
		}

		if migration.version != version+1 {
			return fmt.Errorf("missing migration from version %d to %d", version, migration.version)
		}

		tx, err := conn.BeginTx(ctx, nil)
		if err != nil {
			return err
		}

		if _, err := tx.ExecContext(ctx, migration.sql); err != nil {
			tx.Rollback()
			return fmt.Errorf("apply migration %d: %w", migration.version, err)
		}

		if _, err := tx.ExecContext(ctx, fmt.Sprintf(`PRAGMA user_version = %d`, migration.version)); err != nil {
			tx.Rollback()
			return fmt.Errorf("set user_version %d: %w", migration.version, err)
		}

		if err := tx.Commit(); err != nil {
			return err
		}

		log.Printf("Applied migration: %s", migration.comment)

		version = migration.version
	}

	return nil
}
