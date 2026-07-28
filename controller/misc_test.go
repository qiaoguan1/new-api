package controller

import (
	"os"
	"strings"
	"testing"
)

func TestEmailVerificationUsesNumericGenerator(t *testing.T) {
	source, err := os.ReadFile("misc.go")
	if err != nil {
		t.Fatalf("read misc.go: %v", err)
	}
	if !strings.Contains(string(source), "GenerateNumericVerificationCode(6)") {
		t.Fatal("email verification must use the numeric verification-code generator")
	}
}
