package service

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/pkg/billingexpr"
	"github.com/stretchr/testify/require"
)

func TestGPT61RetailTierAndCacheWriteNormalization(t *testing.T) {
	expr := `len <= 272000 ? tier("base", p * 3 + c * 15 + cr * 0.15 + cc * 3.75) : tier("over_272000", p * 6 + c * 22.5 + cr * 0.3 + cc * 7.5)`
	for _, vector := range []struct {
		prompt, cached, write, output int
		cost                          float64
		tier                          string
	}{
		{272000, 0, 0, 100, 817500, "base"},
		{272001, 0, 0, 100, 1634256, "over_272000"},
		{1000, 200, 300, 10, 2805, "base"},
	} {
		usage := &dto.Usage{PromptTokens: vector.prompt, CompletionTokens: vector.output,
			PromptTokensDetails: dto.InputTokenDetails{CachedTokens: vector.cached, CacheWriteTokens: vector.write, CachedCreationTokens: vector.write}}
		params := BuildTieredTokenParams(usage, false, map[string]bool{"p": true, "c": true, "cr": true, "cc": true, "len": true})
		require.Equal(t, float64(vector.prompt), params.Len)
		require.Equal(t, float64(vector.write), params.CC)
		cost, trace, err := billingexpr.RunExpr(expr, params)
		require.NoError(t, err)
		require.InDelta(t, vector.cost, cost, 0.0001)
		require.Equal(t, vector.tier, trace.MatchedTier)
	}
}
