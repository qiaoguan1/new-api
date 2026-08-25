package service

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/require"
)

func TestSearxStandaloneSearchIntegration(t *testing.T) {
	baseURL := os.Getenv("XT_TEST_SEARX_URL")
	if baseURL == "" {
		t.Skip("XT_TEST_SEARX_URL is not configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	searchResponse, err := ExecuteSearxStandaloneSearch(ctx, baseURL, &dto.SearchRequest{
		ID:       "integration-search",
		Model:    "gpt-5.6-sol",
		Commands: []byte(`{"search_query":[{"q":"中国政府网 人工智能 政策","domains":["gov.cn"]}],"response_length":"short"}`),
	})
	require.NoError(t, err)
	require.NotEmpty(t, searchResponse.Results)

	var first struct {
		RefID string `json:"ref_id"`
		URL   string `json:"url"`
	}
	require.NoError(t, common.Unmarshal(searchResponse.Results[0], &first))
	require.NotEmpty(t, first.RefID)
	require.Contains(t, first.URL, "gov.cn")

	openResponse, err := ExecuteSearxStandaloneSearch(ctx, baseURL, &dto.SearchRequest{
		ID:       "integration-open",
		Model:    "gpt-5.6-sol",
		Commands: []byte(`{"open":[{"ref_id":"` + first.RefID + `"}]}`),
	})
	require.NoError(t, err)
	require.Contains(t, openResponse.Output, "gov.cn")
}
