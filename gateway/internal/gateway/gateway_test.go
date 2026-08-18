package gateway

import (
	"bufio"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestProxyPreservesRequestAndRequestID(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/sessions" || request.URL.RawQuery != "page=2" {
			t.Errorf("upstream request URL = %s, want /api/v1/sessions?page=2", request.URL.String())
		}
		if request.Header.Get("Authorization") != "Bearer example" {
			t.Errorf("Authorization was not forwarded")
		}
		if request.Header.Get("X-Request-ID") != "client-request-1" {
			t.Errorf("X-Request-ID = %q, want client-request-1", request.Header.Get("X-Request-ID"))
		}
		if forwardedFor := request.Header.Get("X-Forwarded-For"); forwardedFor == "" || strings.Contains(forwardedFor, "203.0.113.10") {
			t.Errorf("X-Forwarded-For trusted client input: %q", forwardedFor)
		}
		response.Header().Set("Content-Type", "application/json")
		response.Header().Set("X-Request-ID", "upstream-must-not-override")
		response.WriteHeader(http.StatusCreated)
		_, _ = response.Write([]byte(`{"proxied":true}`))
	}))
	defer upstream.Close()

	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {}))
	defer gateway.Close()
	request, err := http.NewRequest(http.MethodPost, gateway.URL+"/api/v1/sessions?page=2", strings.NewReader(`{"user_id":"user_1"}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer example")
	request.Header.Set("X-Request-ID", "client-request-1")
	request.Header.Set("X-Forwarded-For", "203.0.113.10")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusCreated)
	}
	if response.Header.Get("X-Request-ID") != "client-request-1" {
		t.Fatalf("response X-Request-ID = %q", response.Header.Get("X-Request-ID"))
	}
	if values := response.Header.Values("X-Request-ID"); len(values) != 1 {
		t.Fatalf("response X-Request-ID values = %v, want one value", values)
	}
}

func TestProxyReplacesInvalidRequestID(t *testing.T) {
	requestID := make(chan string, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requestID <- request.Header.Get("X-Request-ID")
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {}))
	defer gateway.Close()

	request, _ := http.NewRequest(http.MethodGet, gateway.URL+"/resource", nil)
	request.Header.Set("X-Request-ID", "invalid request id")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	got := <-requestID
	if !requestIDPattern.MatchString(got) || got == "invalid request id" {
		t.Fatalf("generated request ID = %q", got)
	}
	if response.Header.Get("X-Request-ID") != got {
		t.Fatalf("response request ID = %q, want %q", response.Header.Get("X-Request-ID"), got)
	}
}

func TestProxyStripsClientSuppliedTrustedIdentityHeaders(t *testing.T) {
	received := make(chan http.Header, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		received <- request.Header.Clone()
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {}))
	defer gateway.Close()

	request, _ := http.NewRequest(http.MethodGet, gateway.URL+"/resource", nil)
	request.Header.Set("X-Authenticated-User", "mallory")
	request.Header.Set("X-Gateway-Auth", "forged")
	request.Header.Set(gatewayModeHeader, localGatewayMode)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	headers := <-received
	if headers.Get("X-Authenticated-User") != "" ||
		headers.Get("X-Gateway-Auth") != "" ||
		headers.Get(gatewayModeHeader) != "" {
		t.Fatalf("trusted identity headers reached upstream: %v", headers)
	}
}

func TestProxyFlushesSSEWithoutWaitingForCompletion(t *testing.T) {
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(response, "event: delta\ndata: first\n\n")
		response.(http.Flusher).Flush()
		<-release
		_, _ = io.WriteString(response, "event: done\ndata: {}\n\n")
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {}))
	defer gateway.Close()

	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(gateway.URL + "/api/v1/chat/stream")
	if err != nil {
		close(release)
		t.Fatal(err)
	}
	defer response.Body.Close()
	reader := bufio.NewReader(response.Body)
	line, err := reader.ReadString('\n')
	if err != nil {
		close(release)
		t.Fatal(err)
	}
	if line != "event: delta\n" {
		close(release)
		t.Fatalf("first streamed line = %q", line)
	}
	close(release)
}

func TestBodyLimitRejectsBeforeUpstream(t *testing.T) {
	var upstreamCalls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		upstreamCalls.Add(1)
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {
		config.MaxBodyBytes = 4
	}))
	defer gateway.Close()

	response, err := http.Post(gateway.URL+"/upload", "text/plain", strings.NewReader("12345"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusRequestEntityTooLarge)
	}
	if upstreamCalls.Load() != 0 {
		t.Fatalf("upstream calls = %d, want 0", upstreamCalls.Load())
	}
}

func TestBodyLimitRejectsChunkedRequest(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		_, _ = io.Copy(io.Discard, request.Body)
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {
		config.MaxBodyBytes = 4
	}))
	defer gateway.Close()

	request, err := http.NewRequest(http.MethodPost, gateway.URL+"/upload", strings.NewReader("12345"))
	if err != nil {
		t.Fatal(err)
	}
	request.ContentLength = -1
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusRequestEntityTooLarge)
	}
}

func TestConcurrencyLimitRejectsExcessRequest(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		close(started)
		<-release
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {
		config.MaxConcurrentRequests = 1
	}))
	defer gateway.Close()

	firstDone := make(chan error, 1)
	go func() {
		response, err := http.Get(gateway.URL + "/first")
		if err == nil {
			response.Body.Close()
		}
		firstDone <- err
	}()
	<-started
	response, err := http.Get(gateway.URL + "/second")
	if err != nil {
		close(release)
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusServiceUnavailable {
		response.Body.Close()
		close(release)
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusServiceUnavailable)
	}
	response.Body.Close()
	close(release)
	if err := <-firstDone; err != nil {
		t.Fatal(err)
	}
}

func TestRateLimitReturnsTooManyRequests(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {
		config.RequestsPerSecond = 1
		config.RateLimitBurst = 1
	}))
	defer gateway.Close()

	first, err := http.Get(gateway.URL + "/first")
	if err != nil {
		t.Fatal(err)
	}
	first.Body.Close()
	second, err := http.Get(gateway.URL + "/second")
	if err != nil {
		t.Fatal(err)
	}
	defer second.Body.Close()
	if second.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want %d", second.StatusCode, http.StatusTooManyRequests)
	}
	if second.Header.Get("Retry-After") != "1" {
		t.Fatalf("Retry-After = %q, want 1", second.Header.Get("Retry-After"))
	}
}

func TestHealthAndReadiness(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/health" {
			t.Errorf("readiness path = %q", request.URL.Path)
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstream.URL, func(config *Config) {}))
	defer gateway.Close()

	for path := range map[string]struct{}{"/healthz": {}, "/readyz": {}} {
		response, err := http.Get(gateway.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusOK {
			t.Fatalf("%s status = %d, want %d", path, response.StatusCode, http.StatusOK)
		}
	}
}

func TestReadinessFailsWhenUpstreamIsUnavailable(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	upstreamURL := upstream.URL
	upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstreamURL, func(config *Config) {}))
	defer gateway.Close()

	response, err := http.Get(gateway.URL + "/readyz")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusServiceUnavailable)
	}
}

func TestProxyReturnsBadGatewayWhenUpstreamIsUnavailable(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	upstreamURL := upstream.URL
	upstream.Close()
	gateway := httptest.NewServer(newTestHandler(t, upstreamURL, func(config *Config) {}))
	defer gateway.Close()

	response, err := http.Get(gateway.URL + "/api/v1/sessions")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d, want %d", response.StatusCode, http.StatusBadGateway)
	}
}

func TestShutdownStopsServer(t *testing.T) {
	server := &http.Server{}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := Shutdown(ctx, server); err != nil {
		t.Fatalf("Shutdown returned error: %v", err)
	}
}

func newTestHandler(t *testing.T, upstreamURL string, customize func(*Config)) http.Handler {
	t.Helper()
	parsed, err := url.Parse(upstreamURL)
	if err != nil {
		t.Fatal(err)
	}
	config := Config{
		ListenAddress:         ":0",
		UpstreamURL:           parsed,
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
	}
	customize(&config)
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return NewHandler(config, logger)
}
