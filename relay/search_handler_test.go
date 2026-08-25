package relay

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestReadSearchResponsePreservesOfficialEnvelope(t *testing.T) {
	raw := `{"encrypted_output":"cipher","output":"answer","results":[{"type":"web_result","url":"https://example.com"}]}`
	response := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(raw)),
	}

	parsed, body, apiErr := readSearchResponse(response)
	require.Nil(t, apiErr)
	require.Equal(t, "answer", parsed.Output)
	require.Equal(t, "cipher", parsed.EncryptedOutput)
	require.Len(t, parsed.Results, 1)
	require.JSONEq(t, raw, string(body))
}

func TestReadSearchResponseAllowsExplicitEmptyOutput(t *testing.T) {
	response := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(`{"output":""}`)),
	}

	parsed, _, apiErr := readSearchResponse(response)
	require.Nil(t, apiErr)
	require.Empty(t, parsed.Output)
}

func TestReadSearchResponseRejectsMissingOrInvalidOutput(t *testing.T) {
	for _, raw := range []string{`{"results":[]}`, `{"output":42}`, `not-json`} {
		response := &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(raw))}
		_, _, apiErr := readSearchResponse(response)
		require.NotNil(t, apiErr, raw)
	}
}

func TestReadSearchResponseRejectsOversizedBody(t *testing.T) {
	response := &http.Response{
		StatusCode: http.StatusOK,
		Body:       io.NopCloser(strings.NewReader(`{"output":"` + strings.Repeat("x", maxStandaloneSearchUpstreamResponseSize) + `"}`)),
	}
	_, _, apiErr := readSearchResponse(response)
	require.NotNil(t, apiErr)
}

func TestSearchResponseUsageUsesLocalTokenCounting(t *testing.T) {
	ctx := &gin.Context{}
	usage := service.ResponseText2Usage(ctx, "search result", "gpt-5", 9)
	require.Equal(t, 9, usage.PromptTokens)
	require.Greater(t, usage.CompletionTokens, 0)
	require.Equal(t, usage.PromptTokens+usage.CompletionTokens, usage.TotalTokens)
	require.True(t, common.GetContextKeyBool(ctx, constant.ContextKeyLocalCountTokens))
}

func TestStandaloneSearchSupportsOnlyExplicitSearchCapableAPITypes(t *testing.T) {
	require.True(t, supportsStandaloneSearchAPIType(constant.APITypeCodex))
	require.True(t, supportsStandaloneSearchAPIType(constant.APITypeAdvancedCustom))
	require.False(t, supportsStandaloneSearchAPIType(constant.APITypeOpenAI))
}
