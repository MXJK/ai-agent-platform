package gateway

import (
	"strings"
	"testing"
)

func TestLoadConfigDefaults(t *testing.T) {
	config, err := loadConfig(func(string) (string, bool) { return "", false })
	if err != nil {
		t.Fatalf("loadConfig returned error: %v", err)
	}
	if config.ListenAddress != defaultListenAddress {
		t.Fatalf("ListenAddress = %q, want %q", config.ListenAddress, defaultListenAddress)
	}
	if config.UpstreamURL.String() != defaultUpstreamURL {
		t.Fatalf("UpstreamURL = %q, want %q", config.UpstreamURL, defaultUpstreamURL)
	}
	if config.RequestsPerSecond != 0 {
		t.Fatalf("RequestsPerSecond = %v, want disabled", config.RequestsPerSecond)
	}
	if config.AuthMode != "disabled" {
		t.Fatalf("AuthMode = %q, want disabled", config.AuthMode)
	}
}

func TestLoadConfigRejectsInvalidValues(t *testing.T) {
	tests := map[string]string{
		"GATEWAY_UPSTREAM_URL":            "file:///tmp/socket",
		"GATEWAY_MAX_BODY_BYTES":          "0",
		"GATEWAY_MAX_CONCURRENT_REQUESTS": "many",
		"GATEWAY_REQUESTS_PER_SECOND":     "-1",
		"GATEWAY_UPSTREAM_HEADER_TIMEOUT": "forever",
		"GATEWAY_LOG_LEVEL":               "verbose",
		"GATEWAY_AUTH_MODE":               "basic",
	}
	for name, value := range tests {
		t.Run(name, func(t *testing.T) {
			_, err := loadConfig(func(key string) (string, bool) {
				if key == name {
					return value, true
				}
				return "", false
			})
			if err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("loadConfig error = %v, want error mentioning %s", err, name)
			}
		})
	}
}

func TestLoadConfigAcceptsOIDCIdentityBoundary(t *testing.T) {
	values := map[string]string{
		"GATEWAY_AUTH_MODE":     "oidc",
		"GATEWAY_OIDC_ISSUER":   "https://issuer.example",
		"GATEWAY_OIDC_AUDIENCE": "ai-agent-platform",
		"GATEWAY_OIDC_JWKS_URL": "https://issuer.example/.well-known/jwks.json",
		"GATEWAY_TRUST_SECRET":  "test-trust-secret",
	}
	config, err := loadConfig(func(key string) (string, bool) {
		value, ok := values[key]
		return value, ok
	})
	if err != nil {
		t.Fatalf("loadConfig returned error: %v", err)
	}
	if config.AuthMode != "oidc" || config.OIDCIssuer != values["GATEWAY_OIDC_ISSUER"] {
		t.Fatalf("OIDC config was not loaded: %+v", config)
	}
}

func TestLoadConfigAcceptsLocalIdentityBoundary(t *testing.T) {
	values := map[string]string{
		"GATEWAY_AUTH_MODE":     "local",
		"GATEWAY_LOCAL_USER_ID": "demo_user",
		"GATEWAY_TRUST_SECRET":  "test-trust-secret",
	}
	config, err := loadConfig(func(key string) (string, bool) {
		value, ok := values[key]
		return value, ok
	})
	if err != nil {
		t.Fatalf("loadConfig returned error: %v", err)
	}
	if config.AuthMode != "local" || config.LocalUserID != "demo_user" {
		t.Fatalf("local config was not loaded: %+v", config)
	}
}

func TestLoadConfigRequiresLocalTrustSecret(t *testing.T) {
	_, err := loadConfig(func(key string) (string, bool) {
		if key == "GATEWAY_AUTH_MODE" {
			return "local", true
		}
		return "", false
	})
	if err == nil || !strings.Contains(err.Error(), "GATEWAY_TRUST_SECRET") {
		t.Fatalf("loadConfig error = %v, want missing trust secret", err)
	}
}

func TestLoadConfigRejectsUnsafeLocalUserID(t *testing.T) {
	values := map[string]string{
		"GATEWAY_AUTH_MODE":     "local",
		"GATEWAY_LOCAL_USER_ID": "demo user",
		"GATEWAY_TRUST_SECRET":  "test-trust-secret",
	}
	_, err := loadConfig(func(key string) (string, bool) {
		value, ok := values[key]
		return value, ok
	})
	if err == nil || !strings.Contains(err.Error(), "GATEWAY_LOCAL_USER_ID") {
		t.Fatalf("loadConfig error = %v, want invalid local user ID", err)
	}
}

func TestLoadConfigRequiresCompleteOIDCSettings(t *testing.T) {
	_, err := loadConfig(func(key string) (string, bool) {
		if key == "GATEWAY_AUTH_MODE" {
			return "oidc", true
		}
		return "", false
	})
	if err == nil || !strings.Contains(err.Error(), "GATEWAY_OIDC_JWKS_URL") {
		t.Fatalf("loadConfig error = %v, want missing JWKS URL", err)
	}
}
