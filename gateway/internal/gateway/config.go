package gateway

import (
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	defaultListenAddress               = ":8080"
	defaultUpstreamURL                 = "http://127.0.0.1:8000"
	defaultMaxBodyBytes          int64 = 2 << 20
	defaultMaxConcurrentRequests       = 256
	defaultRateLimitBurst              = 100
	defaultReadHeaderTimeout           = 5 * time.Second
	defaultIdleTimeout                 = 2 * time.Minute
	defaultUpstreamDialTimeout         = 5 * time.Second
	defaultUpstreamHeaderTimeout       = 30 * time.Second
	defaultReadinessTimeout            = 2 * time.Second
	defaultShutdownTimeout             = 10 * time.Second
	defaultOIDCJWKSCacheTTL            = 5 * time.Minute
)

// Config contains process-level gateway settings. A zero RequestsPerSecond
// disables the instance-local rate limiter.
type Config struct {
	ListenAddress         string
	UpstreamURL           *url.URL
	MaxBodyBytes          int64
	MaxConcurrentRequests int
	RequestsPerSecond     float64
	RateLimitBurst        int
	ReadHeaderTimeout     time.Duration
	IdleTimeout           time.Duration
	UpstreamDialTimeout   time.Duration
	UpstreamHeaderTimeout time.Duration
	ReadinessTimeout      time.Duration
	ShutdownTimeout       time.Duration
	LogLevel              string
	AuthMode              string
	OIDCIssuer            string
	OIDCAudience          string
	OIDCJWKSURL           *url.URL
	OIDCJWKSCacheTTL      time.Duration
	GatewayTrustSecret    string
	LocalUserID           string
}

// LoadConfig reads gateway settings from the process environment.
func LoadConfig() (Config, error) {
	return loadConfig(os.LookupEnv)
}

func loadConfig(lookup func(string) (string, bool)) (Config, error) {
	upstream, err := url.Parse(valueOrDefault(lookup, "GATEWAY_UPSTREAM_URL", defaultUpstreamURL))
	if err != nil || upstream.Host == "" || (upstream.Scheme != "http" && upstream.Scheme != "https") {
		return Config{}, fmt.Errorf("GATEWAY_UPSTREAM_URL must be an absolute http(s) URL")
	}
	if upstream.User != nil || upstream.RawQuery != "" || upstream.Fragment != "" {
		return Config{}, fmt.Errorf("GATEWAY_UPSTREAM_URL must not contain credentials, query parameters, or a fragment")
	}

	maxBodyBytes, err := positiveInt64(lookup, "GATEWAY_MAX_BODY_BYTES", defaultMaxBodyBytes)
	if err != nil {
		return Config{}, err
	}
	maxConcurrent, err := positiveInt(lookup, "GATEWAY_MAX_CONCURRENT_REQUESTS", defaultMaxConcurrentRequests)
	if err != nil {
		return Config{}, err
	}
	requestsPerSecond, err := nonNegativeFloat(lookup, "GATEWAY_REQUESTS_PER_SECOND", 0)
	if err != nil {
		return Config{}, err
	}
	rateLimitBurst, err := positiveInt(lookup, "GATEWAY_RATE_LIMIT_BURST", defaultRateLimitBurst)
	if err != nil {
		return Config{}, err
	}
	readHeaderTimeout, err := positiveDuration(lookup, "GATEWAY_READ_HEADER_TIMEOUT", defaultReadHeaderTimeout)
	if err != nil {
		return Config{}, err
	}
	idleTimeout, err := positiveDuration(lookup, "GATEWAY_IDLE_TIMEOUT", defaultIdleTimeout)
	if err != nil {
		return Config{}, err
	}
	upstreamDialTimeout, err := positiveDuration(lookup, "GATEWAY_UPSTREAM_DIAL_TIMEOUT", defaultUpstreamDialTimeout)
	if err != nil {
		return Config{}, err
	}
	upstreamHeaderTimeout, err := positiveDuration(lookup, "GATEWAY_UPSTREAM_HEADER_TIMEOUT", defaultUpstreamHeaderTimeout)
	if err != nil {
		return Config{}, err
	}
	readinessTimeout, err := positiveDuration(lookup, "GATEWAY_READINESS_TIMEOUT", defaultReadinessTimeout)
	if err != nil {
		return Config{}, err
	}
	shutdownTimeout, err := positiveDuration(lookup, "GATEWAY_SHUTDOWN_TIMEOUT", defaultShutdownTimeout)
	if err != nil {
		return Config{}, err
	}
	logLevel := strings.ToLower(valueOrDefault(lookup, "GATEWAY_LOG_LEVEL", "info"))
	if logLevel != "debug" && logLevel != "info" && logLevel != "warn" && logLevel != "error" {
		return Config{}, fmt.Errorf("GATEWAY_LOG_LEVEL must be one of debug, info, warn, or error")
	}
	authMode := strings.ToLower(valueOrDefault(lookup, "GATEWAY_AUTH_MODE", "disabled"))
	if authMode != "disabled" && authMode != "local" && authMode != "oidc" {
		return Config{}, fmt.Errorf("GATEWAY_AUTH_MODE must be disabled, local, or oidc")
	}
	var oidcJWKSURL *url.URL
	oidcIssuer := strings.TrimSpace(valueOrDefault(lookup, "GATEWAY_OIDC_ISSUER", ""))
	oidcAudience := strings.TrimSpace(valueOrDefault(lookup, "GATEWAY_OIDC_AUDIENCE", ""))
	trustSecret := strings.TrimSpace(valueOrDefault(lookup, "GATEWAY_TRUST_SECRET", ""))
	localUserID := strings.TrimSpace(valueOrDefault(lookup, "GATEWAY_LOCAL_USER_ID", "demo_user"))
	jwksCacheTTL, err := positiveDuration(
		lookup,
		"GATEWAY_OIDC_JWKS_CACHE_TTL",
		defaultOIDCJWKSCacheTTL,
	)
	if err != nil {
		return Config{}, err
	}
	if authMode == "oidc" {
		rawJWKSURL := valueOrDefault(lookup, "GATEWAY_OIDC_JWKS_URL", "")
		oidcJWKSURL, err = url.Parse(rawJWKSURL)
		if err != nil || oidcJWKSURL.Host == "" || (oidcJWKSURL.Scheme != "http" && oidcJWKSURL.Scheme != "https") {
			return Config{}, fmt.Errorf("GATEWAY_OIDC_JWKS_URL must be an absolute http(s) URL")
		}
		if oidcIssuer == "" {
			return Config{}, fmt.Errorf("GATEWAY_OIDC_ISSUER is required in oidc mode")
		}
		if oidcAudience == "" {
			return Config{}, fmt.Errorf("GATEWAY_OIDC_AUDIENCE is required in oidc mode")
		}
		if trustSecret == "" {
			return Config{}, fmt.Errorf("GATEWAY_TRUST_SECRET is required in oidc mode")
		}
	}
	if authMode == "local" {
		if trustSecret == "" {
			return Config{}, fmt.Errorf("GATEWAY_TRUST_SECRET is required in local mode")
		}
		if !validLocalUserID(localUserID) {
			return Config{}, fmt.Errorf("GATEWAY_LOCAL_USER_ID must contain 1-256 safe identity characters")
		}
	}

	return Config{
		ListenAddress:         valueOrDefault(lookup, "GATEWAY_LISTEN_ADDRESS", defaultListenAddress),
		UpstreamURL:           upstream,
		MaxBodyBytes:          maxBodyBytes,
		MaxConcurrentRequests: maxConcurrent,
		RequestsPerSecond:     requestsPerSecond,
		RateLimitBurst:        rateLimitBurst,
		ReadHeaderTimeout:     readHeaderTimeout,
		IdleTimeout:           idleTimeout,
		UpstreamDialTimeout:   upstreamDialTimeout,
		UpstreamHeaderTimeout: upstreamHeaderTimeout,
		ReadinessTimeout:      readinessTimeout,
		ShutdownTimeout:       shutdownTimeout,
		LogLevel:              logLevel,
		AuthMode:              authMode,
		OIDCIssuer:            oidcIssuer,
		OIDCAudience:          oidcAudience,
		OIDCJWKSURL:           oidcJWKSURL,
		OIDCJWKSCacheTTL:      jwksCacheTTL,
		GatewayTrustSecret:    trustSecret,
		LocalUserID:           localUserID,
	}, nil
}

func validLocalUserID(value string) bool {
	if value == "" || len(value) > 256 {
		return false
	}
	for _, character := range value {
		if (character >= 'a' && character <= 'z') ||
			(character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') ||
			strings.ContainsRune("._:@/-", character) {
			continue
		}
		return false
	}
	return true
}

func valueOrDefault(lookup func(string) (string, bool), name, fallback string) string {
	if value, ok := lookup(name); ok && strings.TrimSpace(value) != "" {
		return strings.TrimSpace(value)
	}
	return fallback
}

func positiveInt(lookup func(string) (string, bool), name string, fallback int) (int, error) {
	value := valueOrDefault(lookup, name, strconv.Itoa(fallback))
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return parsed, nil
}

func positiveInt64(lookup func(string) (string, bool), name string, fallback int64) (int64, error) {
	value := valueOrDefault(lookup, name, strconv.FormatInt(fallback, 10))
	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil || parsed <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return parsed, nil
}

func nonNegativeFloat(lookup func(string) (string, bool), name string, fallback float64) (float64, error) {
	value := valueOrDefault(lookup, name, strconv.FormatFloat(fallback, 'f', -1, 64))
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil || parsed < 0 {
		return 0, fmt.Errorf("%s must be a non-negative number", name)
	}
	return parsed, nil
}

func positiveDuration(lookup func(string) (string, bool), name string, fallback time.Duration) (time.Duration, error) {
	value := valueOrDefault(lookup, name, fallback.String())
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		return 0, fmt.Errorf("%s must be a positive Go duration", name)
	}
	return parsed, nil
}
