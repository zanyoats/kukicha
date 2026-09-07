package web

import (
	"database/sql"
	"net/http"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func (c *appRoutes) getIdenticon(w http.ResponseWriter, r *http.Request) {
	tx, err := c.db.BeginTx(
		r.Context(),
		&sql.TxOptions{ReadOnly: true},
	)
	if err != nil {
		internalServerError(
			w,
			"unable to begin instance identicon query",
			"error", err,
		)
		return
	}
	defer tx.Rollback()

	identicon, err := query.AppMetadata(
		r.Context(),
		tx,
		app.InstanceIdenticon,
	)
	if err != nil {
		internalServerError(
			w,
			"unable to load instance identicon",
			"error", err,
		)
		return
	}

	if err := tx.Commit(); err != nil {
		internalServerError(
			w,
			"unable to commit instance identicon query",
			"error", err,
		)
		return
	}

	renderImage(w, "image/svg+xml", []byte(identicon))
}
