package ratio_setting

import (
	"math"
	"testing"
)

func TestConfiguredCompletionRatioOverridesFamilyDefault(t *testing.T) {
	original := CompletionRatio2JSONString()
	t.Cleanup(func() {
		if err := UpdateCompletionRatioByJSONString(original); err != nil {
			t.Fatalf("restore completion ratios: %v", err)
		}
	})

	if err := UpdateCompletionRatioByJSONString(`{"gpt-5.6-sol":6}`); err != nil {
		t.Fatalf("configure completion ratio: %v", err)
	}

	if got := GetCompletionRatio("gpt-5.6-sol"); math.Abs(got-6) > 1e-9 {
		t.Fatalf("configured ratio was ignored: got %v, want 6", got)
	}
	info := GetCompletionRatioInfo("gpt-5.6-sol")
	if info.Locked {
		t.Fatalf("configured ratio must be reported as unlocked: %+v", info)
	}
}

func TestCompletionRatioKeepsHardcodedFallbackWhenUnconfigured(t *testing.T) {
	original := CompletionRatio2JSONString()
	t.Cleanup(func() {
		if err := UpdateCompletionRatioByJSONString(original); err != nil {
			t.Fatalf("restore completion ratios: %v", err)
		}
	})

	if err := UpdateCompletionRatioByJSONString(`{}`); err != nil {
		t.Fatalf("clear completion ratios: %v", err)
	}

	if got := GetCompletionRatio("gpt-5.6-sol"); math.Abs(got-8) > 1e-9 {
		t.Fatalf("hard-coded fallback changed: got %v, want 8", got)
	}
	info := GetCompletionRatioInfo("gpt-5.6-sol")
	if !info.Locked {
		t.Fatalf("fallback ratio must remain reported as locked: %+v", info)
	}
}
