package gateway

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync/atomic"
	"testing"
	"time"
)

func TestOIDCMiddlewareValidatesJWTAndInjectsTrustedIdentity(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	jwks := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(response).Encode(map[string]any{
			"keys": []map[string]string{{
				"kty": "RSA",
				"kid": "test-key",
				"alg": "RS256",
				"n":   base64.RawURLEncoding.EncodeToString(privateKey.PublicKey.N.Bytes()),
				"e":   encodeExponent(privateKey.PublicKey.E),
			}},
		})
	}))
	defer jwks.Close()

	received := make(chan http.Header, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		received <- request.Header.Clone()
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(oidcTestHandler(t, upstream.URL, jwks.URL))
	defer gateway.Close()

	token := signTestJWT(t, privateKey, map[string]any{
		"iss": "https://issuer.example",
		"sub": "alice",
		"aud": []string{"another-audience", "ai-agent-platform"},
		"exp": time.Now().Add(time.Minute).Unix(),
		"nbf": time.Now().Add(-time.Minute).Unix(),
	})
	request, _ := http.NewRequest(http.MethodGet, gateway.URL+"/api/v1/workspaces", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("X-Authenticated-User", "mallory")
	request.Header.Set("X-Gateway-Auth", "forged")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusNoContent)
	}
	headers := <-received
	if headers.Get("Authorization") != "" {
		t.Fatalf("Authorization reached upstream")
	}
	if headers.Get("X-Authenticated-User") != "alice" {
		t.Fatalf("trusted subject = %q, want alice", headers.Get("X-Authenticated-User"))
	}
	if headers.Get("X-Gateway-Auth") != "unit-test-trust-secret" {
		t.Fatalf("gateway trust secret was not injected")
	}
}

func TestOIDCMiddlewareRejectsMissingAndInvalidAudienceTokens(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	jwks := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(response).Encode(map[string]any{
			"keys": []map[string]string{{
				"kty": "RSA",
				"kid": "test-key",
				"alg": "RS256",
				"n":   base64.RawURLEncoding.EncodeToString(privateKey.PublicKey.N.Bytes()),
				"e":   encodeExponent(privateKey.PublicKey.E),
			}},
		})
	}))
	defer jwks.Close()
	var upstreamCalls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		upstreamCalls.Add(1)
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(oidcTestHandler(t, upstream.URL, jwks.URL))
	defer gateway.Close()

	missing, err := http.Get(gateway.URL + "/api/v1/sessions")
	if err != nil {
		t.Fatal(err)
	}
	missing.Body.Close()
	if missing.StatusCode != http.StatusUnauthorized {
		t.Fatalf("missing token status = %d, want %d", missing.StatusCode, http.StatusUnauthorized)
	}

	token := signTestJWT(t, privateKey, map[string]any{
		"iss": "https://issuer.example",
		"sub": "alice",
		"aud": "wrong-audience",
		"exp": time.Now().Add(time.Minute).Unix(),
	})
	request, _ := http.NewRequest(http.MethodGet, gateway.URL+"/api/v1/sessions", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	invalid, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	invalid.Body.Close()
	if invalid.StatusCode != http.StatusUnauthorized {
		t.Fatalf("invalid token status = %d, want %d", invalid.StatusCode, http.StatusUnauthorized)
	}
	if upstreamCalls.Load() != 0 {
		t.Fatalf("upstream calls = %d, want 0", upstreamCalls.Load())
	}
}

func TestLocalIdentityMiddlewareReplacesCallerIdentity(t *testing.T) {
	received := make(chan http.Header, 1)
	handler := localIdentityMiddleware("demo_user", "unit-test-trust-secret", http.HandlerFunc(
		func(response http.ResponseWriter, request *http.Request) {
			received <- request.Header.Clone()
			response.WriteHeader(http.StatusNoContent)
		},
	))
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workspaces", nil)
	request.Header.Set("Authorization", "Bearer must-not-pass")
	request.Header.Set("X-Authenticated-User", "mallory")
	request.Header.Set("X-Gateway-Auth", "forged")
	response := httptest.NewRecorder()

	handler.ServeHTTP(response, request)

	if response.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusNoContent)
	}
	headers := <-received
	if headers.Get("Authorization") != "" {
		t.Fatal("Authorization reached upstream")
	}
	if headers.Get("X-Authenticated-User") != "demo_user" {
		t.Fatalf("trusted subject = %q, want demo_user", headers.Get("X-Authenticated-User"))
	}
	if headers.Get("X-Gateway-Auth") != "unit-test-trust-secret" {
		t.Fatal("gateway trust secret was not injected")
	}
}

func oidcTestHandler(t *testing.T, upstreamURL, jwksURL string) http.Handler {
	t.Helper()
	upstream, err := url.Parse(upstreamURL)
	if err != nil {
		t.Fatal(err)
	}
	jwks, err := url.Parse(jwksURL)
	if err != nil {
		t.Fatal(err)
	}
	config := Config{
		ListenAddress:         ":0",
		UpstreamURL:           upstream,
		MaxBodyBytes:          1 << 20,
		MaxConcurrentRequests: 8,
		RateLimitBurst:        8,
		ReadHeaderTimeout:     time.Second,
		IdleTimeout:           time.Second,
		UpstreamDialTimeout:   time.Second,
		UpstreamHeaderTimeout: time.Second,
		ReadinessTimeout:      time.Second,
		ShutdownTimeout:       time.Second,
		LogLevel:              "error",
		AuthMode:              "oidc",
		OIDCIssuer:            "https://issuer.example",
		OIDCAudience:          "ai-agent-platform",
		OIDCJWKSURL:           jwks,
		OIDCJWKSCacheTTL:      time.Minute,
		GatewayTrustSecret:    "unit-test-trust-secret",
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return NewHandler(config, logger)
}

func signTestJWT(t *testing.T, key *rsa.PrivateKey, claims map[string]any) string {
	t.Helper()
	header := map[string]string{"alg": "RS256", "kid": "test-key", "typ": "JWT"}
	encodedHeader := encodeJSONPart(t, header)
	encodedClaims := encodeJSONPart(t, claims)
	signingInput := encodedHeader + "." + encodedClaims
	digest := sha256.Sum256([]byte(signingInput))
	signature, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return signingInput + "." + base64.RawURLEncoding.EncodeToString(signature)
}

func encodeJSONPart(t *testing.T, value any) string {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return base64.RawURLEncoding.EncodeToString(raw)
}

func encodeExponent(value int) string {
	return base64.RawURLEncoding.EncodeToString(big.NewInt(int64(value)).Bytes())
}
