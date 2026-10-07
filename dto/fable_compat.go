package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/tidwall/gjson"
)

// ClaudeFable51Model is the exact model covered by the adaptive-only contract.
const ClaudeFable51Model = "claude-fable-5-1"

// ValidateClaudeFable51Effort checks explicitly supplied Fable effort values.
func ValidateClaudeFable51Effort(effort string) error {
	switch effort {
	case "low", "medium", "high", "max":
		return nil
	default:
		return errors.New("effort must be one of: low, medium, high, max for claude-fable-5-1")
	}
}

// ValidateClaudeFable51JSONFields rejects ambiguous tariff/protocol keys. The
// billing JSON reader and DTO decoder must never select different occurrences.
func ValidateClaudeFable51JSONFields(body []byte, names ...string) error {
	seen := make(map[string]bool, len(names))
	var fieldErr error
	gjson.ParseBytes(body).ForEach(func(key, value gjson.Result) bool {
		for _, name := range names {
			if !strings.EqualFold(key.Str, name) {
				continue
			}
			if key.Str != name {
				fieldErr = fmt.Errorf("%s must use its canonical lowercase field name for claude-fable-5-1", name)
				return false
			}
			if seen[name] {
				fieldErr = fmt.Errorf("duplicate %s field is not supported for claude-fable-5-1", name)
				return false
			}
			seen[name] = true
		}
		return true
	})
	return fieldErr
}

// NormalizeClaudeFable51Request validates the adaptive-only contract without
// changing any other Claude model or dropping native output_config fields.
func NormalizeClaudeFable51Request(request *ClaudeRequest) error {
	if request.Model != ClaudeFable51Model {
		return nil
	}
	if request.Thinking != nil {
		if request.Thinking.Type != "adaptive" {
			return errors.New("thinking.type must be adaptive for claude-fable-5-1")
		}
		if request.Thinking.BudgetTokens != nil {
			return errors.New("thinking.budget_tokens is not supported by claude-fable-5-1; use output_config.effort")
		}
	}
	if len(request.OutputConfig) > 0 {
		if err := ValidateClaudeFable51JSONFields(request.OutputConfig, "effort"); err != nil {
			return fmt.Errorf("output_config: %w", err)
		}
		var config map[string]json.RawMessage
		if err := common.Unmarshal(request.OutputConfig, &config); err != nil || config == nil {
			return errors.New("output_config must be an object for claude-fable-5-1")
		}
		if rawEffort, exists := config["effort"]; exists {
			var effort string
			if err := common.Unmarshal(rawEffort, &effort); err != nil {
				return errors.New("output_config.effort must be a string for claude-fable-5-1")
			}
			if err := ValidateClaudeFable51Effort(effort); err != nil {
				return fmt.Errorf("output_config.effort: %w", err)
			}
		}
	}
	if request.Thinking == nil {
		request.Thinking = &Thinking{Type: "adaptive"}
	}
	return nil
}
