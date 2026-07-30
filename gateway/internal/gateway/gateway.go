package gateway

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"time"
)

var requestIDPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)

type Handler struct {
	config          Config
	logger          *slog.Logger
	proxy           *httputil.ReverseProxy
	readinessClient *http.Client
	concurrency     chan struct{}
	rateLimiter     *tokenBucket
	authenticator   *oidcAuthenticator
}

func NewHandler(config Config, logger *slog.Logger) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: config.UpstreamDialTimeout, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          config.MaxConcurrentRequests * 2,
		MaxIdleConnsPerHost:   config.MaxConcurrentRequests,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: config.UpstreamHeaderTimeout,
		ExpectContinueTimeout: time.Second,
	}
	proxy := httputil.NewSingleHostReverseProxy(config.UpstreamURL)
	originalDirector := proxy.Director
	proxy.Director = func(request *http.Request) {
		originalHost := request.Host
		originalDirector(request)
		// This service is the public edge by default. Do not trust forwarding
		// headers supplied by an arbitrary client.
		request.Header.Del("Forwarded")
		request.Header.Del("X-Forwarded-For")
		request.Host = config.UpstreamURL.Host
		request.Header.Set("X-Forwarded-Host", originalHost)
		if request.TLS == nil {
			request.Header.Set("X-Forwarded-Proto", "http")
		} else {
			request.Header.Set("X-Forwarded-Proto", "https")
		}
	}
	proxy.Transport = transport
	proxy.FlushInterval = -1
	proxy.ErrorHandler = func(response http.ResponseWriter, request *http.Request, err error) {
		var bodyTooLarge *http.MaxBytesError
		if errors.As(err, &bodyTooLarge) {
			writeJSON(response, http.StatusRequestEntityTooLarge, "request_too_large", "request body exceeds configured limit")
			return
		}
		logger.Error("upstream request failed", "request_id", request.Header.Get("X-Request-ID"), "error", err)
		writeJSON(response, http.StatusBadGateway, "upstream_unavailable", "upstream service is unavailable")
	}

	handler := &Handler{
		config:          config,
		logger:          logger,
		proxy:           proxy,
		readinessClient: &http.Client{Transport: transport, Timeout: config.ReadinessTimeout},
		concurrency:     make(chan struct{}, config.MaxConcurrentRequests),
	}
	if config.RequestsPerSecond > 0 {
		handler.rateLimiter = newTokenBucket(config.RequestsPerSecond, config.RateLimitBurst)
	}
	if config.AuthMode == "oidc" {
		handler.authenticator = newOIDCAuthenticator(config, nil)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", handler.health)
	mux.HandleFunc("GET /readyz", handler.ready)
	proxyHandler := http.Handler(proxy)
	if handler.authenticator != nil {
		proxyHandler = handler.authenticator.middleware(proxyHandler)
	} else {
		proxyHandler = stripUntrustedIdentityHeaders(proxyHandler)
	}
	mux.Handle("/", handler.withRequestID(handler.withAccessLog(handler.withBodyLimit(handler.withRateLimit(handler.withConcurrency(proxyHandler))))))
	return mux
}

func stripUntrustedIdentityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		request.Header.Del("X-Authenticated-User")
		request.Header.Del("X-Gateway-Auth")
		next.ServeHTTP(response, request)
	})
}

func NewServer(config Config, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              config.ListenAddress,
		Handler:           handler,
		ReadHeaderTimeout: config.ReadHeaderTimeout,
		IdleTimeout:       config.IdleTimeout,
		// WriteTimeout remains zero because a valid SSE stream may outlive any
		// fixed request timeout. Upstream header and shutdown timeouts are bounded.
		WriteTimeout:   0,
		MaxHeaderBytes: 1 << 20,
	}
}

func (handler *Handler) health(response http.ResponseWriter, _ *http.Request) {
	writeJSON(response, http.StatusOK, "ok", "gateway is running")
}

func (handler *Handler) ready(response http.ResponseWriter, request *http.Request) {
	probeURL := handler.config.UpstreamURL.ResolveReference(&urlReferenceHealth).String()
	probe, err := http.NewRequestWithContext(request.Context(), http.MethodGet, probeURL, nil)
	if err != nil {
		writeJSON(response, http.StatusServiceUnavailable, "not_ready", "upstream health URL is invalid")
		return
	}
	upstreamResponse, err := handler.readinessClient.Do(probe)
	if err != nil {
		writeJSON(response, http.StatusServiceUnavailable, "not_ready", "upstream service is unavailable")
		return
	}
	defer upstreamResponse.Body.Close()
	if upstreamResponse.StatusCode < 200 || upstreamResponse.StatusCode >= 300 {
		writeJSON(response, http.StatusServiceUnavailable, "not_ready", "upstream health check failed")
		return
	}
	writeJSON(response, http.StatusOK, "ready", "gateway and upstream are ready")
}

var urlReferenceHealth = mustParseReference("/api/v1/health")

func mustParseReference(path string) url.URL {
	reference, err := url.Parse(path)
	if err != nil {
		panic(err)
	}
	return *reference
}

func (handler *Handler) withRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requestID := request.Header.Get("X-Request-ID")
		if !requestIDPattern.MatchString(requestID) {
			requestID = newRequestID()
		}
		request.Header.Set("X-Request-ID", requestID)
		next.ServeHTTP(&requestIDResponseWriter{ResponseWriter: response, requestID: requestID}, request)
	})
}

func (handler *Handler) withAccessLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		started := time.Now()
		recorder := &statusRecorder{ResponseWriter: response, status: http.StatusOK}
		next.ServeHTTP(recorder, request)
		handler.logger.Info(
			"gateway request completed",
			"request_id", request.Header.Get("X-Request-ID"),
			"method", request.Method,
			"path", request.URL.Path,
			"status_code", recorder.status,
			"duration_ms", time.Since(started).Milliseconds(),
		)
	})
}

func (handler *Handler) withBodyLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.ContentLength > handler.config.MaxBodyBytes {
			writeJSON(response, http.StatusRequestEntityTooLarge, "request_too_large", "request body exceeds configured limit")
			return
		}
		if request.Body != nil {
			request.Body = http.MaxBytesReader(response, request.Body, handler.config.MaxBodyBytes)
		}
		next.ServeHTTP(response, request)
	})
}

func (handler *Handler) withRateLimit(next http.Handler) http.Handler {
	if handler.rateLimiter == nil {
		return next
	}
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if !handler.rateLimiter.allow(time.Now()) {
			response.Header().Set("Retry-After", "1")
			writeJSON(response, http.StatusTooManyRequests, "rate_limited", "gateway request rate exceeded")
			return
		}
		next.ServeHTTP(response, request)
	})
}

func (handler *Handler) withConcurrency(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		select {
		case handler.concurrency <- struct{}{}:
			defer func() { <-handler.concurrency }()
			next.ServeHTTP(response, request)
		default:
			response.Header().Set("Retry-After", "1")
			writeJSON(response, http.StatusServiceUnavailable, "gateway_overloaded", "gateway concurrency limit exceeded")
		}
	})
}

func newRequestID() string {
	random := make([]byte, 8)
	if _, err := rand.Read(random); err != nil {
		return fmt.Sprintf("gw_%d", time.Now().UnixNano())
	}
	return "gw_" + hex.EncodeToString(random)
}

func writeJSON(response http.ResponseWriter, status int, code, message string) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(map[string]string{"status": code, "message": message})
}

type statusRecorder struct {
	http.ResponseWriter
	status      int
	wroteHeader bool
}

type requestIDResponseWriter struct {
	http.ResponseWriter
	requestID   string
	wroteHeader bool
}

func (writer *requestIDResponseWriter) WriteHeader(status int) {
	if writer.wroteHeader {
		return
	}
	writer.wroteHeader = true
	writer.Header().Set("X-Request-ID", writer.requestID)
	writer.ResponseWriter.WriteHeader(status)
}

func (writer *requestIDResponseWriter) Write(body []byte) (int, error) {
	if !writer.wroteHeader {
		writer.WriteHeader(http.StatusOK)
	}
	return writer.ResponseWriter.Write(body)
}

func (writer *requestIDResponseWriter) Flush() {
	if !writer.wroteHeader {
		writer.WriteHeader(http.StatusOK)
	}
	_ = http.NewResponseController(writer.ResponseWriter).Flush()
}

func (writer *requestIDResponseWriter) Unwrap() http.ResponseWriter {
	return writer.ResponseWriter
}

func (recorder *statusRecorder) WriteHeader(status int) {
	if recorder.wroteHeader {
		return
	}
	recorder.wroteHeader = true
	recorder.status = status
	recorder.ResponseWriter.WriteHeader(status)
}

func (recorder *statusRecorder) Write(body []byte) (int, error) {
	if !recorder.wroteHeader {
		recorder.WriteHeader(http.StatusOK)
	}
	return recorder.ResponseWriter.Write(body)
}

func (recorder *statusRecorder) Flush() {
	_ = http.NewResponseController(recorder.ResponseWriter).Flush()
}

func (recorder *statusRecorder) Unwrap() http.ResponseWriter {
	return recorder.ResponseWriter
}

// Shutdown bounds graceful draining while allowing in-flight SSE clients to
// finish when they disconnect before the deadline.
func Shutdown(ctx context.Context, server *http.Server) error {
	return server.Shutdown(ctx)
}
