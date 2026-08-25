package dto

import (
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestSearchRequestPreservesOfficialFields(t *testing.T) {
	raw := []byte(`{
		"id":"search_123",
		"model":"gpt-5.6-sol",
		"reasoning":{"effort":"medium"},
		"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"latest policy"}]}],
		"commands":{"search_query":[{"q":"latest policy","recency":0}],"response_length":"short"},
		"settings":{"external_web_access":"live","image_settings":{"caption":false}},
		"max_output_tokens":0
	}`)

	var request SearchRequest
	require.NoError(t, common.Unmarshal(raw, &request))
	require.Equal(t, "search_123", request.ID)
	require.Equal(t, "gpt-5.6-sol", request.Model)
	require.NotNil(t, request.MaxOutputTokens)
	require.Zero(t, *request.MaxOutputTokens)

	encoded, err := common.Marshal(request)
	require.NoError(t, err)
	require.Contains(t, string(encoded), `"recency":0`)
	require.Contains(t, string(encoded), `"caption":false`)
	require.Contains(t, string(encoded), `"max_output_tokens":0`)

	ctx, _ := gin.CreateTestContext(httptest.NewRecorder())
	require.False(t, request.IsStream(ctx))
	meta := request.GetTokenCountMeta()
	require.Contains(t, meta.CombineText, "latest policy")
	require.Zero(t, meta.MaxTokens)
	require.Equal(t, "medium", request.SearchContextSize())

	request.Settings = []byte(`{"search_context_size":"high"}`)
	require.Equal(t, "high", request.SearchContextSize())
	request.Settings = []byte(`{"search_context_size":"future"}`)
	require.Equal(t, "medium", request.SearchContextSize())
	request.Settings = []byte(`{"filters":{"allowed_domains":["openai.com"],"blocked_domains":["example.com"]}}`)
	allowed, blocked := request.SearchDomainFilters()
	require.Equal(t, []string{"openai.com"}, allowed)
	require.Equal(t, []string{"example.com"}, blocked)
	request.Settings = []byte(`{"external_web_access":false}`)
	require.False(t, request.ExternalWebAccessEnabled())
	request.Settings = []byte(`{"external_web_access":"live"}`)
	require.True(t, request.ExternalWebAccessEnabled())

	request.SetModelName("gpt-5.6-terra")
	require.Equal(t, "gpt-5.6-terra", request.Model)
}

func TestSearchResponsePreservesOpaqueResults(t *testing.T) {
	raw := []byte(`{"encrypted_output":"cipher","output":"answer","results":[{"type":"computer_initialize_state","future":true}]}`)
	var response SearchResponse
	require.NoError(t, common.Unmarshal(raw, &response))
	require.Equal(t, "cipher", response.EncryptedOutput)
	require.Equal(t, "answer", response.Output)
	require.Len(t, response.Results, 1)

	encoded, err := common.Marshal(response)
	require.NoError(t, err)
	require.JSONEq(t, string(raw), string(encoded))
}
