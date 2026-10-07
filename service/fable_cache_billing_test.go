package service

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/pkg/billingexpr"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestFable51TieredCacheCreationPartitions(t *testing.T) {
	const expression = `tier("standard", p*133.9+c*669.5+cr*3.3475+cc*167.375+cc1h*267.8)*(param("output_config.effort")=="max"?3:1)`
	vectors := []struct {
		name     string
		total    int
		alias    int
		fiveM    int
		oneH     int
		wantFive float64
		wantHour float64
		wantLen  float64
		quota    int
		max      bool
	}{
		{name: "aggregate only", total: 80, wantFive: 80, wantLen: 230, quota: 3025},
		{name: "five minute only", total: 80, fiveM: 80, wantFive: 80, wantLen: 230, quota: 3025},
		{name: "one hour only", total: 80, oneH: 80, wantHour: 80, wantLen: 230, quota: 3628},
		{name: "mixed ttl", total: 80, fiveM: 50, oneH: 30, wantFive: 50, wantHour: 30, wantLen: 230, quota: 3251},
		{name: "unclassified remainder", total: 100, fiveM: 20, oneH: 30, wantFive: 70, wantHour: 30, wantLen: 250, quota: 3502},
		{name: "split without aggregate", fiveM: 50, oneH: 30, wantFive: 50, wantHour: 30, wantLen: 230, quota: 3251},
		{name: "aggregate below split", total: 60, fiveM: 50, oneH: 30, wantFive: 50, wantHour: 30, wantLen: 230, quota: 3251},
		{name: "duplicate aggregate aliases", total: 80, alias: 80, wantFive: 80, wantLen: 230, quota: 3025},
		{name: "no cache creation", wantLen: 150, quota: 2021},
		{name: "max preserves cache partition", total: 100, fiveM: 20, oneH: 30, wantFive: 70, wantHour: 30, wantLen: 250, quota: 10507, max: true},
	}
	for _, vector := range vectors {
		t.Run(vector.name, func(t *testing.T) {
			usage := dto.Usage{
				PromptTokens:                100,
				CompletionTokens:            20,
				UsageSemantic:               "anthropic",
				PromptTokensDetails:         dto.InputTokenDetails{CachedTokens: 50, CachedCreationTokens: vector.total, CacheWriteTokens: vector.alias},
				ClaudeCacheCreation5mTokens: vector.fiveM,
				ClaudeCacheCreation1hTokens: vector.oneH,
			}
			original := usage
			params := BuildModelTieredTokenParams("claude-fable-5-1", &usage, true, billingexpr.UsedVars(expression))
			assert.Equal(t, float64(100), params.P, "Claude input excludes cache already")
			assert.Equal(t, vector.wantFive, params.CC)
			assert.Equal(t, vector.wantHour, params.CC1h)
			assert.Equal(t, vector.wantLen, params.Len)
			assert.Equal(t, original, usage, "settlement must not mutate upstream/display usage")
			relayInfo := &relaycommon.RelayInfo{
				OriginModelName: "claude-fable-5-1",
				TieredBillingSnapshot: &billingexpr.BillingSnapshot{
					BillingMode:  "tiered_expr",
					ModelName:    "claude-fable-5-1",
					ExprString:   expression,
					ExprHash:     billingexpr.ExprHashString(expression),
					GroupRatio:   0.15,
					QuotaPerUnit: 500000,
				},
			}
			if vector.max {
				relayInfo.BillingRequestInput = &billingexpr.RequestInput{Body: []byte(`{"output_config":{"effort":"max"}}`)}
			}
			ok, quota, result := TryTieredSettle(relayInfo, params)
			require.True(t, ok)
			require.NotNil(t, result)
			assert.Equal(t, vector.quota, quota)
		})
	}
}

func TestFable51CacheCompatibilityLeavesOtherModelsAndOpenAIUsageUnchanged(t *testing.T) {
	usedVars := map[string]bool{"p": true, "c": true, "cr": true, "cc": true, "cc1h": true}
	usage := dto.Usage{
		PromptTokens:                100,
		CompletionTokens:            20,
		UsageSemantic:               "anthropic",
		PromptTokensDetails:         dto.InputTokenDetails{CachedTokens: 50, CachedCreationTokens: 100},
		ClaudeCacheCreation5mTokens: 20,
		ClaudeCacheCreation1hTokens: 30,
	}
	for _, modelName := range []string{"claude-fable-5", "claude-opus-4-8"} {
		t.Run(modelName, func(t *testing.T) {
			assert.Equal(t, BuildTieredTokenParams(&usage, true, usedVars),
				BuildModelTieredTokenParams(modelName, &usage, true, usedVars))
		})
	}
	usage.UsageSemantic = "openai"
	usage.PromptTokens = 250
	assert.Equal(t, BuildTieredTokenParams(&usage, false, usedVars),
		BuildModelTieredTokenParams("claude-fable-5-1", &usage, false, usedVars))
}
