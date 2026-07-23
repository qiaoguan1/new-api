package openaicompat

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
)

func TestResponsesResponseToChatCompletionsResponseCopiesCost(t *testing.T) {
	resp := &dto.OpenAIResponsesResponse{
		CreatedAt: 123,
		Model:     "gpt-4.1",
		Usage: &dto.Usage{
			InputTokens:  10,
			OutputTokens: 20,
			TotalTokens:  30,
			Cost:         0.95,
			InputTokensDetails: &dto.InputTokenDetails{
				CachedTokens: 4,
				AudioTokens:  5,
			},
			CompletionTokenDetails: dto.OutputTokenDetails{
				ReasoningTokens: 6,
			},
		},
	}

	_, usage, err := ResponsesResponseToChatCompletionsResponse(resp, "chatcmpl-test")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if usage == nil {
		t.Fatal("usage is nil")
	}
	cost, ok := usage.Cost.(float64)
	if !ok {
		t.Fatalf("expected float64 cost, got %T", usage.Cost)
	}
	if cost != 0.95 {
		t.Fatalf("expected cost 0.95, got %v", cost)
	}
}
