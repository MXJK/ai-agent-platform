package gateway

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

var (
	errMissingBearer = errors.New("bearer token is required")
	errInvalidToken  = errors.New("bearer token is invalid")
)

const (
	gatewayModeHeader = "X-Gateway-Mode"
	localGatewayMode  = "local"
)

type oidcAuthenticator struct {
	issuer      string
	audience    string
	jwksURL     string
	cacheTTL    time.Duration
	trustSecret string
	client      *http.Client

	mutex     sync.RWMutex
	keys      map[string]*rsa.PublicKey
	fetchedAt time.Time
}

type jwtHeader struct {
	Algorithm string `json:"alg"`
	KeyID     string `json:"kid"`
}

type jwtClaims struct {
	Issuer    string          `json:"iss"`
	Subject   string          `json:"sub"`
	Audience  json.RawMessage `json:"aud"`
	Expires   json.Number     `json:"exp"`
	NotBefore json.Number     `json:"nbf"`
}

type jwksDocument struct {
	Keys []jwk `json:"keys"`
}

type jwk struct {
	KeyType   string `json:"kty"`
	KeyID     string `json:"kid"`
	Algorithm string `json:"alg"`
	Modulus   string `json:"n"`
	Exponent  string `json:"e"`
}

func newOIDCAuthenticator(config Config, client *http.Client) *oidcAuthenticator {
	if client == nil {
		client = &http.Client{Timeout: config.ReadinessTimeout}
	}
	return &oidcAuthenticator{
		issuer:      config.OIDCIssuer,
		audience:    config.OIDCAudience,
		jwksURL:     config.OIDCJWKSURL.String(),
		cacheTTL:    config.OIDCJWKSCacheTTL,
		trustSecret: config.GatewayTrustSecret,
		client:      client,
		keys:        make(map[string]*rsa.PublicKey),
	}
}

func localIdentityMiddleware(userID, trustSecret string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		// Local mode is intentionally passwordless. Its security boundary is the
		// loopback-only publish rule in docker-compose.yml, so caller-provided
		// identity, capability assertions and credentials must never reach the
		// trusted upstream.
		request.Header.Del("Authorization")
		request.Header.Del("X-Authenticated-User")
		request.Header.Del("X-Gateway-Auth")
		request.Header.Del(gatewayModeHeader)
		request.Header.Set("X-Authenticated-User", userID)
		request.Header.Set("X-Gateway-Auth", trustSecret)
		request.Header.Set(gatewayModeHeader, localGatewayMode)
		next.ServeHTTP(response, request)
	})
}

func (auth *oidcAuthenticator) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		request.Header.Del("X-Authenticated-User")
		request.Header.Del("X-Gateway-Auth")
		request.Header.Del(gatewayModeHeader)
		token, err := bearerToken(request.Header.Get("Authorization"))
		if err != nil {
			writeJSON(response, http.StatusUnauthorized, "unauthorized", err.Error())
			return
		}
		subject, err := auth.verify(request.Context(), token, time.Now())
		if err != nil {
			writeJSON(response, http.StatusUnauthorized, "unauthorized", "bearer token is invalid")
			return
		}
		request.Header.Del("Authorization")
		request.Header.Set("X-Authenticated-User", subject)
		request.Header.Set("X-Gateway-Auth", auth.trustSecret)
		next.ServeHTTP(response, request)
	})
}

func (auth *oidcAuthenticator) verify(ctx context.Context, token string, now time.Time) (string, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", errInvalidToken
	}
	var header jwtHeader
	if err := decodeJWTPart(parts[0], &header); err != nil || header.Algorithm != "RS256" || header.KeyID == "" {
		return "", errInvalidToken
	}
	key, err := auth.key(ctx, header.KeyID, now)
	if err != nil {
		return "", errInvalidToken
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return "", errInvalidToken
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], signature); err != nil {
		return "", errInvalidToken
	}
	var claims jwtClaims
	if err := decodeJWTPart(parts[1], &claims); err != nil {
		return "", errInvalidToken
	}
	expires, err := claims.Expires.Int64()
	if err != nil || now.Unix() >= expires {
		return "", errInvalidToken
	}
	if claims.NotBefore != "" {
		notBefore, err := claims.NotBefore.Int64()
		if err != nil || now.Unix() < notBefore {
			return "", errInvalidToken
		}
	}
	if claims.Issuer != auth.issuer || claims.Subject == "" || !audienceContains(claims.Audience, auth.audience) {
		return "", errInvalidToken
	}
	if len(claims.Subject) > 256 {
		return "", errInvalidToken
	}
	return claims.Subject, nil
}

func (auth *oidcAuthenticator) key(ctx context.Context, keyID string, now time.Time) (*rsa.PublicKey, error) {
	auth.mutex.RLock()
	key := auth.keys[keyID]
	fresh := now.Sub(auth.fetchedAt) < auth.cacheTTL
	auth.mutex.RUnlock()
	if key != nil && fresh {
		return key, nil
	}
	if err := auth.refresh(ctx, now); err != nil {
		return nil, err
	}
	auth.mutex.RLock()
	defer auth.mutex.RUnlock()
	key = auth.keys[keyID]
	if key == nil {
		return nil, fmt.Errorf("OIDC signing key %q was not found", keyID)
	}
	return key, nil
}

func (auth *oidcAuthenticator) refresh(ctx context.Context, now time.Time) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, auth.jwksURL, nil)
	if err != nil {
		return err
	}
	response, err := auth.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("JWKS endpoint returned HTTP %d", response.StatusCode)
	}
	var document jwksDocument
	if err := json.NewDecoder(response.Body).Decode(&document); err != nil {
		return err
	}
	keys := make(map[string]*rsa.PublicKey)
	for _, item := range document.Keys {
		if item.KeyType != "RSA" || item.KeyID == "" || (item.Algorithm != "" && item.Algorithm != "RS256") {
			continue
		}
		key, err := rsaKey(item.Modulus, item.Exponent)
		if err == nil {
			keys[item.KeyID] = key
		}
	}
	if len(keys) == 0 {
		return errors.New("JWKS endpoint returned no usable RSA keys")
	}
	auth.mutex.Lock()
	auth.keys = keys
	auth.fetchedAt = now
	auth.mutex.Unlock()
	return nil
}

func bearerToken(header string) (string, error) {
	parts := strings.Fields(header)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") || parts[1] == "" {
		return "", errMissingBearer
	}
	return parts[1], nil
}

func decodeJWTPart(part string, target any) error {
	value, err := base64.RawURLEncoding.DecodeString(part)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(strings.NewReader(string(value)))
	decoder.UseNumber()
	return decoder.Decode(target)
}

func audienceContains(raw json.RawMessage, expected string) bool {
	var single string
	if json.Unmarshal(raw, &single) == nil {
		return single == expected
	}
	var multiple []string
	if json.Unmarshal(raw, &multiple) != nil {
		return false
	}
	for _, value := range multiple {
		if value == expected {
			return true
		}
	}
	return false
}

func rsaKey(modulus, exponent string) (*rsa.PublicKey, error) {
	modulusBytes, err := base64.RawURLEncoding.DecodeString(modulus)
	if err != nil {
		return nil, err
	}
	exponentBytes, err := base64.RawURLEncoding.DecodeString(exponent)
	if err != nil || len(exponentBytes) == 0 || len(exponentBytes) > 4 {
		return nil, errInvalidToken
	}
	exponentValue := 0
	for _, value := range exponentBytes {
		exponentValue = exponentValue<<8 | int(value)
	}
	if exponentValue < 3 {
		return nil, errInvalidToken
	}
	return &rsa.PublicKey{N: new(big.Int).SetBytes(modulusBytes), E: exponentValue}, nil
}
