package common

import (
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	relayconstant "github.com/QuantumNous/new-api/relay/constant"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestGenRelayInfoSearchRecordsOneSearchCallWithRequestedContext(t *testing.T) {
	ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
	ctx.Request = httptest.NewRequest("POST", "/v1/alpha/search", nil)
	request := &dto.SearchRequest{
		Model:    "gpt-5.6-sol",
		Settings: []byte(`{"search_context_size":"high"}`),
	}

	info := GenRelayInfoSearch(ctx, request)
	require.Equal(t, relayconstant.RelayModeSearch, info.RelayMode)
	require.Equal(t, types.RelayFormat(types.RelayFormatOpenAISearch), info.RelayFormat)
	tool := info.ResponsesUsageInfo.BuiltInTools[dto.BuildInToolWebSearchPreview]
	require.Equal(t, 1, tool.CallCount)
	require.Equal(t, "high", tool.SearchContextSize)
}
