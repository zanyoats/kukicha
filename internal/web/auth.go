package web

import (
	"bytes"
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/auth"
)

type sessionContextKey struct{}

func (c *webRouter) cookieName() string {
	return "kukicha"
}

func (c *webRouter) sessionCookie(r *http.Request, value string) *http.Cookie {
	return &http.Cookie{
		Name:     c.cookieName(),
		Value:    value,
		Path:     "/",
		HttpOnly: true,
		// HTTPs reaches go directly or is handled by a proxy which preserves the
		// browser's Origin for POSTs
		Secure:   r.TLS != nil || r.Header.Get("Origin") == "https://"+r.Host,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   int(c.auth.SessionLifetime().Seconds()),
		Expires:  time.Now().Add(c.auth.SessionLifetime()),
	}
}

func (c *webRouter) requireAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		publicRead := r.Method == http.MethodGet || r.Method == http.MethodHead
		if (r.URL.Path == c.urls.Login && (publicRead || r.Method == http.MethodPost)) ||
			(publicRead && (strings.HasPrefix(r.URL.Path, assetsRoot) || r.URL.Path == c.urls.Favicon || r.URL.Path == c.urls.InstanceIdenticon)) {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		if cookie, err := r.Cookie(c.cookieName()); err == nil {
			if claims, err := c.auth.Validate(cookie.Value); err == nil {
				next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), sessionContextKey{}, claims)))
				return
			}
		}
		// Clients making background requests can navigate to login without replaying them.
		w.Header().Set("X-Kukicha-Login", c.urls.Login)
		// API calls
		apiPath := strings.TrimSuffix(c.urls.SubmitJob, "jobs/")
		if strings.HasPrefix(r.URL.Path, apiPath) {
			apiError(w, http.StatusUnauthorized, "Authentication required")
			return
		}
		// Page navigation redirects
		if publicRead && strings.Contains(r.Header.Get("Accept"), "text/html") &&
			r.URL.Path != c.urls.TrackAudio && r.URL.Path != c.urls.AlbumArtwork {
			http.Redirect(w, r, c.urls.Login, http.StatusSeeOther)
			return
		}
		// Audio streaming and artwork requests
		http.Error(w, "Authentication required", http.StatusUnauthorized)
	})
}

type loginViewModel struct {
	URLs  URLs
	Error string
}

func (c *webRouter) login(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method == http.MethodGet || r.Method == http.MethodHead {
		// Redirect responses are followed internally; Unpoly sees this final response.
		w.Header().Set("X-Kukicha-Login", c.urls.Login)
	}
	status, message := http.StatusOK, ""
	if r.Method == http.MethodPost {
		r.Body = http.MaxBytesReader(w, r.Body, app.LoginRequestMaxBytes)
		if err := r.ParseForm(); err != nil {
			status, message = http.StatusBadRequest, "Invalid login form"
		} else {
			passphrase := r.PostForm.Get("passphrase")
			if passphrase != "" && len(passphrase) <= auth.MaxPassphraseBytes && c.auth.VerifyPassphrase(passphrase) {
				token, err := c.auth.Issue(time.Now())
				if err != nil {
					internalServerError(w, "issue session", "error", err)
					return
				}
				http.SetCookie(w, c.sessionCookie(r, token))
				http.Redirect(w, r, c.urls.Home, http.StatusSeeOther)
				return
			}
			status, message = http.StatusUnauthorized, "Invalid passphrase."
		}
	}
	var body bytes.Buffer
	if err := c.renderer.Render(&body, "login.gohtml", loginViewModel{URLs: c.urls, Error: message}); err != nil {
		internalServerError(w, "render login", "error", err)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_, _ = body.WriteTo(w)
}

func (c *webRouter) logout(w http.ResponseWriter, r *http.Request) {
	cookie := c.sessionCookie(r, "")
	cookie.MaxAge = -1
	cookie.Expires = time.Unix(1, 0)
	http.SetCookie(w, cookie)
	http.Redirect(w, r, c.urls.Login, http.StatusSeeOther)
}
