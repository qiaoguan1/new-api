package service

import "testing"

func TestCalculateQuotaFromUpstreamCost(t *testing.T) {
	got := calculateQuotaFromUpstreamCost(0.49237)
	if got != 246185 {
		t.Fatalf("expected 246185, got %d", got)
	}
}
