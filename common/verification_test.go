package common

import (
	"bytes"
	"testing"
)

func TestGenerateNumericVerificationCodeUsesOnlyDigits(t *testing.T) {
	reader := bytes.NewReader([]byte{250, 0, 1, 9, 10, 249, 255})

	code, err := generateNumericVerificationCode(reader, 5)
	if err != nil {
		t.Fatalf("generateNumericVerificationCode returned an error: %v", err)
	}
	if code != "01909" {
		t.Fatalf("expected deterministic numeric code 01909, got %q", code)
	}
}

func TestGenerateNumericVerificationCodeRejectsNonPositiveLength(t *testing.T) {
	if _, err := generateNumericVerificationCode(bytes.NewReader(nil), 0); err == nil {
		t.Fatal("expected zero length to be rejected")
	}
	if _, err := generateNumericVerificationCode(bytes.NewReader(nil), -1); err == nil {
		t.Fatal("expected negative length to be rejected")
	}
}

func TestGenerateNumericVerificationCodePropagatesReaderFailure(t *testing.T) {
	if _, err := generateNumericVerificationCode(bytes.NewReader(nil), 1); err == nil {
		t.Fatal("expected random reader failure to be returned")
	}
}
