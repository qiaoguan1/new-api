package channel

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestImageTimeoutDoesNotMutateSharedClientOrTextRequests(t *testing.T) {
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/generations", nil)
	req := httptest.NewRequest(http.MethodPost, "https://example.invalid/v1/images/generations", nil)
	shared := &http.Client{Timeout: 0, Transport: http.DefaultTransport}
	_, imageClient := imageRequestClient(c, req, shared)
	require.Equal(t, 600*time.Second, imageClient.Timeout)
	require.Equal(t, time.Duration(0), shared.Timeout)
	require.Same(t, shared.Transport, imageClient.Transport)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/responses", nil)
	textReq, textClient := imageRequestClient(c, req, shared)
	require.Same(t, shared, textClient)
	require.Same(t, req, textReq)
}
