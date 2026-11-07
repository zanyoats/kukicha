package web

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

func (c *webRouter) showHelp(w http.ResponseWriter, r *http.Request) {
	claims, ok := r.Context().Value(sessionContextKey{}).(*jwt.RegisteredClaims)
	if !ok {
		http.Error(w, "Authentication required", http.StatusUnauthorized)
		return
	}
	now := time.Now()
	renderPage(w, c.renderer, "help.gohtml", struct {
		Configuration                                               []helpConfigSection
		URLs                                                        URLs
		EyeBrow, Heading, IssuedAt, ExpiresAt, UserAgent, Remaining string
	}{
		Configuration: runtimeConfiguration(c.conf),
		URLs:          c.urls, EyeBrow: "Settings", Heading: "Help",
		IssuedAt:  claims.IssuedAt.UTC().Format(time.RFC3339),
		ExpiresAt: claims.ExpiresAt.UTC().Format(time.RFC3339),
		UserAgent: r.UserAgent(), Remaining: formatSessionRemaining(claims.ExpiresAt.Sub(now)),
	})
}

func formatSessionRemaining(remaining time.Duration) string {
	if remaining <= 0 {
		return "Expired"
	}
	if remaining < time.Minute {
		return "Less than a minute"
	}
	minutes := int64(remaining / time.Minute)
	var parts []string
	for _, unit := range []struct {
		name  string
		value int64
	}{
		{"day", minutes / (24 * 60)}, {"hour", minutes / 60 % 24}, {"minute", minutes % 60},
	} {
		if unit.value == 0 {
			continue
		}
		name := unit.name
		if unit.value != 1 {
			name += "s"
		}
		parts = append(parts, fmt.Sprintf("%d %s", unit.value, name))
	}
	return strings.Join(parts, ", ")
}
