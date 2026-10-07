package oaichat

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFable51ChatReasoningUsesAdaptiveEffort(t *testing.T) {
	for _, effort := range []string{"", "low", "medium", "high", "max"} {
		t.Run("effort="+effort, func(t *testing.T) {
			request, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
				Model: "claude-fable-5-1", ReasoningEffort: effort,
				Messages: []dto.Message{{Role: "user", Content: "hello"}},
			})
			require.NoError(t, err)
			require.NotNil(t, request.Thinking)
			assert.Equal(t, "adaptive", request.Thinking.Type)
			assert.Nil(t, request.Thinking.BudgetTokens)
			wire, err := common.Marshal(request)
			require.NoError(t, err)
			assert.NotContains(t, string(wire), "budget_tokens")
			if effort == "" {
				assert.Empty(t, request.OutputConfig)
			} else {
				var config dto.OutputConfigForEffort
				require.NoError(t, common.Unmarshal(request.OutputConfig, &config))
				assert.Equal(t, effort, config.Effort)
			}
		})
	}
}

func TestFable51ChatConversionRejectsUnsupportedReasoning(t *testing.T) {
	for _, effort := range []string{"none", "minimal", "MAX"} {
		t.Run(effort, func(t *testing.T) {
			_, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
				Model: "claude-fable-5-1", ReasoningEffort: effort,
			})
			require.ErrorContains(t, err, "reasoning_effort")
		})
	}
	t.Run("legacy OpenRouter budget", func(t *testing.T) {
		_, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
			Model: "claude-fable-5-1", Reasoning: []byte(`{"max_tokens":2048}`),
		})
		require.ErrorContains(t, err, "reasoning_effort")
	})
}

func TestFable51CompatibilityDoesNotChangeOtherClaudeModels(t *testing.T) {
	for _, model := range []string{"claude-sonnet-4-5", "claude-fable-5", "claude-fable-5-1-other"} {
		t.Run(model, func(t *testing.T) {
			request, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
				Model: model, ReasoningEffort: "low",
			})
			require.NoError(t, err)
			require.NotNil(t, request.Thinking)
			assert.Equal(t, "enabled", request.Thinking.Type)
			require.NotNil(t, request.Thinking.BudgetTokens)
			assert.Equal(t, 1280, *request.Thinking.BudgetTokens)
			assert.Empty(t, request.OutputConfig)
		})
	}
}

func TestFable51CompletionTokenAliasWireContract(t *testing.T) {
	request, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
		Model: "claude-fable-5-1", MaxCompletionTokens: common.GetPointer[uint](128),
	})
	require.NoError(t, err)
	wire, err := common.Marshal(request)
	require.NoError(t, err)
	var decoded struct {
		MaxTokens uint `json:"max_tokens"`
	}
	require.NoError(t, common.Unmarshal(wire, &decoded))
	assert.Equal(t, uint(128), decoded.MaxTokens)
	assert.NotContains(t, string(wire), "max_completion_tokens")

	t.Run("Fable conflicting aliases rejected", func(t *testing.T) {
		_, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
			Model: "claude-fable-5-1", MaxTokens: common.GetPointer[uint](8192), MaxCompletionTokens: common.GetPointer[uint](128),
		})
		require.ErrorContains(t, err, "max_completion_tokens")
	})
	t.Run("other model keeps completion token priority", func(t *testing.T) {
		request, err := OpenAIChatRequestToClaudeMessages(nil, dto.GeneralOpenAIRequest{
			Model: "claude-sonnet-4-5", MaxTokens: common.GetPointer[uint](8192), MaxCompletionTokens: common.GetPointer[uint](128),
		})
		require.NoError(t, err)
		require.NotNil(t, request.MaxTokens)
		assert.Equal(t, uint(128), *request.MaxTokens)
	})
}
