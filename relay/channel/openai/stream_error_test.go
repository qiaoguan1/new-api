package openai

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParseOpenAIStreamErrorRecognizesJSONRPCConcurrencyFailure(t *testing.T) {
	err := parseOpenAIStreamError(`{"code":-32603,"message":"Internal error","data":{"details":"Concurrency limit exceeded for account, please retry later","category":"internal"}}`)

	require.NotNil(t, err)
	assert.Equal(t, http.StatusTooManyRequests, err.StatusCode)
	assert.Contains(t, err.Error(), "Concurrency limit exceeded")
}

func TestParseOpenAIStreamErrorIgnoresNormalCompletionChunk(t *testing.T) {
	err := parseOpenAIStreamError(`{"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"ok"}}]}`)

	assert.Nil(t, err)
}

func TestParseOpenAIStreamErrorBoundsUpstreamMessage(t *testing.T) {
	err := parseOpenAIStreamError(`{"code":-32603,"message":"` + strings.Repeat("x", maxStreamErrorInspectionBytes+100) + `"}`)

	require.NotNil(t, err)
	assert.LessOrEqual(t, len(err.Error()), maxStreamErrorMessageBytes+len("upstream stream error: ")+len("..."))
}

func TestOaiStreamHandlerReturnsInitialStreamErrorWithoutForwardingOrUsage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	info := &relaycommon.RelayInfo{
		IsStream:    true,
		DisablePing: true,
		RelayFormat: types.RelayFormatOpenAI,
		ChannelMeta: &relaycommon.ChannelMeta{
			UpstreamModelName: "gpt-5.6-sol",
		},
	}
	info.StreamStatus = relaycommon.NewStreamStatus()
	info.StreamStatus.RecordError("previous channel error")
	response := &http.Response{
		StatusCode: http.StatusOK,
		Header:     make(http.Header),
		Body: io.NopCloser(strings.NewReader(
			"data: {\"code\":-32603,\"message\":\"Internal error\",\"data\":{\"details\":\"Concurrency limit exceeded for account, please retry later\",\"category\":\"internal\"}}\n\n" +
				"data: [DONE]\n\n",
		)),
	}

	usage, err := OaiStreamHandler(context, info, response)

	require.NotNil(t, err)
	assert.Equal(t, http.StatusTooManyRequests, err.StatusCode)
	assert.Nil(t, usage)
	assert.Empty(t, recorder.Body.String())
	require.NotNil(t, info.StreamStatus)
	assert.True(t, info.StreamStatus.HasErrors())
	assert.Equal(t, 1, info.StreamStatus.TotalErrorCount())
	assert.NotContains(t, info.StreamStatus.Errors[0].Message, "previous channel")
}
