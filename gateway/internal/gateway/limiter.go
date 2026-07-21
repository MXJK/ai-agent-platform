package gateway

import (
	"sync"
	"time"
)

// tokenBucket is intentionally process-local. It protects one replica from
// bursts; distributed tenant quotas belong in a shared rate-limit service.
type tokenBucket struct {
	mu       sync.Mutex
	rate     float64
	capacity float64
	tokens   float64
	updated  time.Time
}

func newTokenBucket(rate float64, burst int) *tokenBucket {
	return &tokenBucket{
		rate:     rate,
		capacity: float64(burst),
		tokens:   float64(burst),
		updated:  time.Now(),
	}
}

func (bucket *tokenBucket) allow(now time.Time) bool {
	bucket.mu.Lock()
	defer bucket.mu.Unlock()

	elapsed := now.Sub(bucket.updated).Seconds()
	if elapsed > 0 {
		bucket.tokens = min(bucket.capacity, bucket.tokens+elapsed*bucket.rate)
		bucket.updated = now
	}
	if bucket.tokens < 1 {
		return false
	}
	bucket.tokens--
	return true
}
