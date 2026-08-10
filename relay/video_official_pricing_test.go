package relay

import (
	"testing"

	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestOfficialVideoQuoteUsesArkOfficialTimesOnePointFive(t *testing.T) {
	quota, sku, matched, err := officialVideoQuote("seedance2.0-selfsur-720p", "", 4)
	require.NoError(t, err)
	require.True(t, matched)
	assert.Equal(t, "seedance-2.0", sku.model)
	assert.Equal(t, "720p", sku.resolution)
	assert.Equal(t, 2_980_800, quota)
}

func TestApprovedVideoAliasCannotFallThroughToGenericPricing(t *testing.T) {
	quota, sku, matched, err := officialVideoQuote("value-sd-premium-720p", "720p", 4)
	require.NoError(t, err)
	require.True(t, matched)
	assert.Equal(t, "seedance-2.0", sku.model)
	assert.Equal(t, 2_980_800, quota)
}

func TestOfficialVideoQuoteSupportsStableModelResolution(t *testing.T) {
	quota, sku, matched, err := officialVideoQuote("seedance-2.0-mini", "1280x720", 5)
	require.NoError(t, err)
	require.True(t, matched)
	assert.Equal(t, "720p", sku.resolution)
	assert.Equal(t, 1_863_000, quota)
}

func TestOfficialVideoQuoteRoundsOnceFor480P(t *testing.T) {
	quota, _, matched, err := officialVideoQuote("seedance-2.0", "480p", 4)
	require.NoError(t, err)
	require.True(t, matched)
	assert.Equal(t, 1_325_835, quota)
}

func TestOfficialVideoQuoteFailsClosedForMissingResolutionOrDuration(t *testing.T) {
	_, _, matched, err := officialVideoQuote("seedance-2.0", "", 5)
	assert.True(t, matched)
	require.Error(t, err)

	_, _, matched, err = officialVideoQuote("sd2-fast-720p", "", 0)
	assert.True(t, matched)
	require.Error(t, err)

	_, _, matched, err = officialVideoQuote("sd2-fast-720p", "4k", 4)
	assert.True(t, matched)
	require.Error(t, err)
}

func TestOfficialVideoQuoteLeavesUnrelatedModelsUntouched(t *testing.T) {
	_, _, matched, err := officialVideoQuote("sora-2", "720p", 4)
	require.NoError(t, err)
	assert.False(t, matched)
}

func TestOfficialVideoReservationCannotBeBypassedByFreeModelSetting(t *testing.T) {
	c := &gin.Context{}
	c.Set("task_request", relaycommon.TaskSubmitReq{Model: "sd2-720p", Resolution: "720p", Duration: 4})
	info := &relaycommon.RelayInfo{OriginModelName: "sd2-720p"}
	info.PriceData.FreeModel = true

	matched, err := applyOfficialVideoReservation(c, info)
	require.NoError(t, err)
	require.True(t, matched)
	assert.False(t, info.PriceData.FreeModel)
	assert.Equal(t, 2_980_800, info.PriceData.Quota)
}
