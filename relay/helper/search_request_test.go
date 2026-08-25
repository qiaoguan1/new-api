package helper

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func searchTestContext(body string) *gin.Context {
	recorder := httptest.NewRecorder()
	ctx, _ := gin.CreateTestContext(recorder)
	ctx.Request = httptest.NewRequest(http.MethodPost, "/v1/alpha/search", bytes.NewBufferString(body))
	ctx.Request.Header.Set("Content-Type", "application/json")
	return ctx
}

func TestGetAndValidateSearchRequest(t *testing.T) {
	request, err := GetAndValidateSearchRequest(searchTestContext(`{"id":"search_1","model":"gpt-5.6-sol","commands":{"search_query":[{"q":"XT"}]}}`))
	require.NoError(t, err)
	require.Equal(t, "search_1", request.ID)
	require.Equal(t, "gpt-5.6-sol", request.Model)
}

func TestGetAndValidateSearchRequestRejectsMissingRequiredFields(t *testing.T) {
	_, err := GetAndValidateSearchRequest(searchTestContext(`{"model":"gpt-5.6-sol"}`))
	require.EqualError(t, err, "id is required")

	_, err = GetAndValidateSearchRequest(searchTestContext(`{"id":"search_1"}`))
	require.EqualError(t, err, "model is required")
}

func TestGetAndValidateSearchRequestRejectsUnboundedMaxOutput(t *testing.T) {
	_, err := GetAndValidateSearchRequest(searchTestContext(`{"id":"search_1","model":"gpt-5.6-sol","max_output_tokens":2147483648}`))
	require.EqualError(t, err, "max_output_tokens is invalid")
}
