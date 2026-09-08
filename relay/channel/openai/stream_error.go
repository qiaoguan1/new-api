package openai

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/types"
)

const (
	maxStreamErrorInspectionBytes = 4 << 10
	maxStreamErrorMessageBytes    = 1 << 10
)

type openAIStreamErrorEnvelope struct {
	Code    json.RawMessage `json:"code"`
	Message string          `json:"message"`
	Type    string          `json:"type"`
	Error   *struct {
		Code    json.RawMessage `json:"code"`
		Message string          `json:"message"`
	} `json:"error"`
	Data *struct {
		Details  string `json:"details"`
		Category string `json:"category"`
	} `json:"data"`
}

func nonZeroJSONCode(code json.RawMessage) bool {
	normalized := strings.TrimSpace(string(code))
	return normalized != "" && normalized != "null" && normalized != "0" && normalized != `""`
}

func boundedStreamErrorMessage(message string) string {
	message = strings.TrimSpace(message)
	if len(message) > maxStreamErrorInspectionBytes {
		end := maxStreamErrorInspectionBytes
		for end > 0 && !utf8.ValidString(message[:end]) {
			end--
		}
		message = message[:end]
	}
	message = strings.Join(strings.Fields(message), " ")
	if len(message) <= maxStreamErrorMessageBytes {
		return message
	}
	end := maxStreamErrorMessageBytes
	for end > 0 && !utf8.ValidString(message[:end]) {
		end--
	}
	return message[:end] + "..."
}

// parseOpenAIStreamError detects structured upstream failures delivered inside
// an HTTP 200 event stream. It deliberately ignores ordinary completion chunks.
func parseOpenAIStreamError(data string) *types.NewAPIError {
	var envelope openAIStreamErrorEnvelope
	if err := json.Unmarshal([]byte(data), &envelope); err != nil {
		return nil
	}

	message := strings.TrimSpace(envelope.Message)
	hasErrorCode := nonZeroJSONCode(envelope.Code)
	if envelope.Error != nil {
		if nested := strings.TrimSpace(envelope.Error.Message); nested != "" {
			message = nested
		}
		hasErrorCode = hasErrorCode || nonZeroJSONCode(envelope.Error.Code)
	}
	if envelope.Data != nil {
		if details := strings.TrimSpace(envelope.Data.Details); details != "" {
			message = details
		}
	}
	message = boundedStreamErrorMessage(message)

	isErrorEnvelope := envelope.Error != nil ||
		strings.EqualFold(strings.TrimSpace(envelope.Type), "error") ||
		(hasErrorCode && message != "")
	if !isErrorEnvelope || message == "" {
		return nil
	}

	status := http.StatusBadGateway
	lowerMessage := strings.ToLower(message)
	switch {
	case strings.Contains(lowerMessage, "concurrency limit"),
		strings.Contains(lowerMessage, "rate limit"),
		strings.Contains(lowerMessage, "too many requests"):
		status = http.StatusTooManyRequests
	case strings.Contains(lowerMessage, "temporarily unavailable"),
		strings.Contains(lowerMessage, "service unavailable"),
		strings.Contains(lowerMessage, "overloaded"):
		status = http.StatusServiceUnavailable
	}

	return types.NewOpenAIError(
		fmt.Errorf("upstream stream error: %s", message),
		types.ErrorCodeBadResponse,
		status,
	)
}
