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
}

func TestLoadConfigRejectsInvalidValues(t *testing.T) {
	tests := map[string]string{
		"GATEWAY_UPSTREAM_URL":            "file:///tmp/socket",
		"GATEWAY_MAX_BODY_BYTES":          "0",
		"GATEWAY_MAX_CONCURRENT_REQUESTS": "many",
		"GATEWAY_REQUESTS_PER_SECOND":     "-1",
		"GATEWAY_UPSTREAM_HEADER_TIMEOUT": "forever",
		"GATEWAY_LOG_LEVEL":               "verbose",
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
