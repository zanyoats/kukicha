package web

import (
	"fmt"
	"net/http"
	"strings"
)

func NewHandler(appBasePath string) http.Handler {
	mux := http.NewServeMux()

	routePattern := appBasePath
	if strings.HasSuffix(routePattern, "/") {
		routePattern += "{$}"
	}

	mux.HandleFunc("GET "+routePattern, appRoot)

	return mux
}

func appRoot(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)

	_, _ = fmt.Fprint(w, "Kukicha\n")
}
