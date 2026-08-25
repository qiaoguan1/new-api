package dto

import (
	"encoding/json"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/samber/lo"
)

// SearchRequest mirrors the standalone search wire contract used by Codex.
// Opaque fields intentionally remain RawMessage so newer command and result
// variants can pass through the relay without a coordinated gateway release.
type SearchRequest struct {
	ID              string          `json:"id"`
	Model           string          `json:"model"`
	Reasoning       json.RawMessage `json:"reasoning,omitempty"`
	Input           json.RawMessage `json:"input,omitempty"`
	Commands        json.RawMessage `json:"commands,omitempty"`
	Settings        json.RawMessage `json:"settings,omitempty"`
	MaxOutputTokens *uint           `json:"max_output_tokens,omitempty"`
}

func (r *SearchRequest) GetTokenCountMeta() *types.TokenCountMeta {
	parts := make([]string, 0, 4)
	for _, value := range []json.RawMessage{r.Input, r.Commands, r.Settings, r.Reasoning} {
		if len(value) > 0 {
			parts = append(parts, string(value))
		}
	}
	return &types.TokenCountMeta{
		TokenType:   types.TokenTypeTokenizer,
		CombineText: strings.Join(parts, "\n"),
		MaxTokens:   int(lo.FromPtrOr(r.MaxOutputTokens, uint(0))),
	}
}

func (r *SearchRequest) IsStream(*gin.Context) bool {
	return false
}

func (r *SearchRequest) SetModelName(modelName string) {
	if modelName != "" {
		r.Model = modelName
	}
}

type SearchResponse struct {
	EncryptedOutput string            `json:"encrypted_output,omitempty"`
	Output          string            `json:"output"`
	Results         []json.RawMessage `json:"results,omitempty"`
}

func (r *SearchRequest) SearchContextSize() string {
	if len(r.Settings) == 0 {
		return "medium"
	}
	var settings struct {
		SearchContextSize string `json:"search_context_size"`
	}
	if err := common.Unmarshal(r.Settings, &settings); err != nil {
		return "medium"
	}
	switch settings.SearchContextSize {
	case "low", "medium", "high":
		return settings.SearchContextSize
	default:
		return "medium"
	}
}

func (r *SearchRequest) SearchDomainFilters() (allowed []string, blocked []string) {
	if len(r.Settings) == 0 {
		return nil, nil
	}
	var settings struct {
		Filters struct {
			AllowedDomains []string `json:"allowed_domains"`
			BlockedDomains []string `json:"blocked_domains"`
		} `json:"filters"`
	}
	if err := common.Unmarshal(r.Settings, &settings); err != nil {
		return nil, nil
	}
	return settings.Filters.AllowedDomains, settings.Filters.BlockedDomains
}

func (r *SearchRequest) ExternalWebAccessEnabled() bool {
	if len(r.Settings) == 0 {
		return true
	}
	var settings struct {
		ExternalWebAccess json.RawMessage `json:"external_web_access"`
	}
	if err := common.Unmarshal(r.Settings, &settings); err != nil || len(settings.ExternalWebAccess) == 0 {
		return true
	}
	var enabled bool
	if err := common.Unmarshal(settings.ExternalWebAccess, &enabled); err == nil {
		return enabled
	}
	var mode string
	if err := common.Unmarshal(settings.ExternalWebAccess, &mode); err == nil {
		return mode == "cached" || mode == "indexed" || mode == "live"
	}
	return false
}
