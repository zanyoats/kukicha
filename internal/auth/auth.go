// Package auth implements passphrase verification and stateless browser sessions.
package auth

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// MaxPassphraseBytes keeps configured credentials within the login form limit.
const MaxPassphraseBytes = 4096

// Auth keeps only an in-memory passphrase digest and an independent signing key.
type Auth struct {
	passphraseDigest [sha256.Size]byte
	secret           []byte
	sessionLifetime  time.Duration
}

// New requires a passphrase and a decoded secret validated by ParseJWTSecret.
func New(passphrase string, secret []byte, sessionLifetime time.Duration) (*Auth, error) {
	if passphrase == "" {
		return nil, errors.New("KUKICHA_PASSPHRASE must be set")
	}
	if len(passphrase) > MaxPassphraseBytes {
		return nil, errors.New("KUKICHA_PASSPHRASE exceeds 4096 bytes")
	}
	if sessionLifetime < time.Second {
		return nil, errors.New("session lifetime must be at least one second")
	}
	return &Auth{
		passphraseDigest: sha256.Sum256([]byte(passphrase)),
		secret:           bytes.Clone(secret),
		sessionLifetime:  sessionLifetime,
	}, nil
}

// ParseJWTSecret accepts standard Base64 with or without padding. The caller
// must generate the secret randomly; encoding and length cannot prove entropy.
// One common way to generate the secret: `openssl rand -base64 32`
func ParseJWTSecret(secret string) ([]byte, error) {
	if secret == "" {
		return nil, errors.New("KUKICHA_JWT_SECRET must be set")
	}
	encoding := base64.RawStdEncoding.Strict()
	if strings.HasSuffix(secret, "=") {
		encoding = base64.StdEncoding.Strict()
	}
	key, err := encoding.DecodeString(secret)
	if err != nil || strings.ContainsAny(secret, "\r\n") {
		return nil, errors.New("KUKICHA_JWT_SECRET must be valid standard Base64")
	}
	if len(key) < 32 {
		return nil, errors.New("KUKICHA_JWT_SECRET must decode to at least 32 random bytes")
	}
	return key, nil
}

// SessionLifetime returns the lifetime used for newly issued sessions.
func (a *Auth) SessionLifetime() time.Duration {
	return a.sessionLifetime
}

func (a *Auth) VerifyPassphrase(passphrase string) bool {
	// Fixed-size digests allow constant-time comparison regardless of input length.
	// This digest is never persisted and is not a password-storage hash.
	digest := sha256.Sum256([]byte(passphrase))
	return subtle.ConstantTimeCompare(a.passphraseDigest[:], digest[:]) == 1
}

func (a *Auth) Issue(now time.Time) (string, error) {
	claims := jwt.RegisteredClaims{
		Issuer: "kukicha", Subject: "passphrase", ID: rand.Text(),
		IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(now.Add(a.sessionLifetime)),
	}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(a.secret)
}

func (a *Auth) Validate(value string) (*jwt.RegisteredClaims, error) {
	if len(value) > 4096 {
		return nil, errors.New("session token too large")
	}
	claims := new(jwt.RegisteredClaims)
	token, err := jwt.ParseWithClaims(value, claims, func(*jwt.Token) (any, error) { return a.secret, nil },
		jwt.WithValidMethods([]string{"HS256"}), jwt.WithExpirationRequired(), jwt.WithIssuedAt(),
		jwt.WithIssuer("kukicha"), jwt.WithSubject("passphrase"), jwt.WithStrictDecoding())
	if err != nil {
		return nil, err
	}
	if !token.Valid || claims.IssuedAt == nil || claims.ExpiresAt == nil || !claims.ExpiresAt.After(claims.IssuedAt.Time) {
		return nil, fmt.Errorf("invalid session claims")
	}
	return claims, nil
}
