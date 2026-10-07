package helper

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/tidwall/gjson"
)

// This rollout supports a conservative 128000-token output limit, scoped only
// to Fable 5.1 rather than changing the other models' existing token bounds.
const fable51MaxOutputTokens = 128000

// Fable51RequestContextKey identifies Fable request-validation failures for the
// controller's scoped HTTP 400 response; other models retain their status codes.
const Fable51RequestContextKey = "fable51_request"

// validateFable51RelayEndpoint limits this rollout to the two protocols whose
// request conversion and billing semantics have been verified before charging.
func validateFable51RelayEndpoint(c *gin.Context, format types.RelayFormat) error {
	path := c.Request.URL.Path
	usesFable := c.GetString("original_model") == dto.ClaudeFable51Model
	if !usesFable && strings.HasPrefix(c.Request.Header.Get("Content-Type"), "application/json") {
		var body json.RawMessage
		if err := common.UnmarshalBodyReusable(c, &body); err != nil {
			// Preserve each non-Fable protocol's existing malformed-body errors.
			return nil
		}
		gjson.ParseBytes(body).ForEach(func(key, value gjson.Result) bool {
			// Check every occurrence, not just gjson's first or the DTO's last.
			if strings.EqualFold(key.Str, "model") && value.Str == dto.ClaudeFable51Model {
				usesFable = true
				return false
			}
			return true
		})
	}
	if usesFable {
		c.Set(Fable51RequestContextKey, true)
	}
	if (path == "/v1/chat/completions" && format == types.RelayFormatOpenAI) ||
		(path == "/v1/messages" && format == types.RelayFormatClaude) {
		return nil
	}
	if usesFable {
		return fmt.Errorf("claude-fable-5-1 only supports /v1/chat/completions and /v1/messages in this rollout")
	}
	return nil
}

// validateFable51ProtocolFields runs on the original body before pricing or
// pre-consumption. Fields silently dropped by a DTO must not drive billing.
func validateFable51ProtocolFields(c *gin.Context, model string, native bool) error {
	if model != dto.ClaudeFable51Model && c.GetString("original_model") != dto.ClaudeFable51Model {
		return nil
	}
	c.Set(Fable51RequestContextKey, true)
	var body json.RawMessage
	if err := common.UnmarshalBodyReusable(c, &body); err != nil {
		return err
	}
	protocolFields := []string{"model", "reasoning_effort", "output_config", "thinking", "reasoning", "enable_thinking", "max_tokens", "max_completion_tokens"}
	if native {
		protocolFields = append(protocolFields, "max_tokens_to_sample")
	}
	if err := dto.ValidateClaudeFable51JSONFields(body, protocolFields...); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := common.Unmarshal(body, &fields); err != nil {
		return err
	}
	if !native {
		_, hasMaxTokens := fields["max_tokens"]
		_, hasMaxCompletionTokens := fields["max_completion_tokens"]
		if hasMaxTokens && hasMaxCompletionTokens {
			return fmt.Errorf("max_tokens and max_completion_tokens must not both be supplied for claude-fable-5-1")
		}
	}
	for _, field := range []string{"max_tokens", "max_completion_tokens"} {
		rawTokens, exists := fields[field]
		if !exists {
			if native && field == "max_tokens" {
				return fmt.Errorf("max_tokens is required for claude-fable-5-1 Messages requests")
			}
			continue
		}
		var tokens uint
		if err := common.Unmarshal(rawTokens, &tokens); err != nil || tokens == 0 || tokens > fable51MaxOutputTokens {
			return fmt.Errorf("%s must be an integer between 1 and %d for claude-fable-5-1", field, fable51MaxOutputTokens)
		}
	}
	forbidden := []string{"output_config", "thinking", "reasoning", "enable_thinking"}
	if native {
		forbidden = []string{"reasoning_effort", "reasoning", "enable_thinking", "max_tokens_to_sample", "max_completion_tokens"}
	}
	for _, field := range forbidden {
		if _, exists := fields[field]; exists {
			if native {
				return fmt.Errorf("%s is not supported in claude-fable-5-1 Messages requests; use thinking.type=adaptive and output_config.effort", field)
			}
			return fmt.Errorf("%s is not supported in claude-fable-5-1 Chat requests; use reasoning_effort", field)
		}
	}
	if native {
		if thinking, exists := fields["thinking"]; exists {
			if err := dto.ValidateClaudeFable51JSONFields(thinking, "type", "budget_tokens", "display"); err != nil {
				return fmt.Errorf("thinking: %w", err)
			}
		}
	}
	if !native {
		if rawEffort, exists := fields["reasoning_effort"]; exists {
			var effort string
			if err := common.Unmarshal(rawEffort, &effort); err != nil {
				return fmt.Errorf("reasoning_effort must be a string for claude-fable-5-1")
			}
			if err := dto.ValidateClaudeFable51Effort(effort); err != nil {
				return fmt.Errorf("reasoning_effort: %w", err)
			}
		}
	}
	return nil
}
