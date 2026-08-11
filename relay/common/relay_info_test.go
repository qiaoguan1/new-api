package common

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestTaskSubmitReqPromotesXingTuTopLevelVideoFieldsIntoMetadata(t *testing.T) {
	var request TaskSubmitReq
	err := json.Unmarshal([]byte(`{
		"provider_id":"video-aixingtu-api",
		"request_id":"req_20260811_000001",
		"model":"seedance-2.0",
		"aspect_ratio":"16:9",
		"generate_audio":false,
		"metadata":{"camera_fixed":true}
	}`), &request)
	require.NoError(t, err)

	require.Equal(t, "video-aixingtu-api", request.ProviderID)
	require.Equal(t, "req_20260811_000001", request.RequestID)
	require.Equal(t, "16:9", request.AspectRatio)
	require.NotNil(t, request.GenerateAudio)
	require.False(t, *request.GenerateAudio)
	require.Equal(t, "16:9", request.Metadata["aspect_ratio"])
	require.Equal(t, false, request.Metadata["generate_audio"])
	require.Equal(t, true, request.Metadata["camera_fixed"])
}

func TestWriteTaskSubmitResponseIsSuppressedOnlyForXingTuContract(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Set(XingTuVideoContractContextKey, true)
	WriteTaskSubmitResponse(context, http.StatusOK, map[string]string{"id": "legacy"})
	require.Empty(t, recorder.Body.String())

	legacyRecorder := httptest.NewRecorder()
	legacyContext, _ := gin.CreateTestContext(legacyRecorder)
	WriteTaskSubmitResponse(legacyContext, http.StatusOK, map[string]string{"id": "legacy"})
	require.JSONEq(t, `{"id":"legacy"}`, legacyRecorder.Body.String())
}

func TestRelayInfoGetFinalRequestRelayFormatPrefersExplicitFinal(t *testing.T) {
	info := &RelayInfo{
		RelayFormat:             types.RelayFormatOpenAI,
		RequestConversionChain:  []types.RelayFormat{types.RelayFormatOpenAI, types.RelayFormatClaude},
		FinalRequestRelayFormat: types.RelayFormatOpenAIResponses,
	}

	require.Equal(t, types.RelayFormat(types.RelayFormatOpenAIResponses), info.GetFinalRequestRelayFormat())
}

func TestRelayInfoGetFinalRequestRelayFormatFallsBackToConversionChain(t *testing.T) {
	info := &RelayInfo{
		RelayFormat:            types.RelayFormatOpenAI,
		RequestConversionChain: []types.RelayFormat{types.RelayFormatOpenAI, types.RelayFormatClaude},
	}

	require.Equal(t, types.RelayFormat(types.RelayFormatClaude), info.GetFinalRequestRelayFormat())
}

func TestRelayInfoGetFinalRequestRelayFormatFallsBackToRelayFormat(t *testing.T) {
	info := &RelayInfo{
		RelayFormat: types.RelayFormatGemini,
	}

	require.Equal(t, types.RelayFormat(types.RelayFormatGemini), info.GetFinalRequestRelayFormat())
}

func TestRelayInfoGetFinalRequestRelayFormatNilReceiver(t *testing.T) {
	var info *RelayInfo
	require.Equal(t, types.RelayFormat(""), info.GetFinalRequestRelayFormat())
}
