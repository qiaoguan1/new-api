package middleware

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestXingTuVideoBodyLimitRejectsAbove256KiBBeforeHandler(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	called := false
	router.POST("/v1/videos", XingTuVideoBodyLimit(), func(c *gin.Context) {
		called = true
		c.Status(http.StatusNoContent)
	})

	body := bytes.Repeat([]byte("x"), XingTuVideoMaxRequestBytes+1)
	request := httptest.NewRequest(http.MethodPost, "/v1/videos", bytes.NewReader(body))
	request.Header.Set("X-XingTu-Contract-Version", "xtai-video-billing-v2.1")
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusRequestEntityTooLarge, recorder.Code)
	require.False(t, called)
	require.Contains(t, recorder.Body.String(), `"code":"request_too_large"`)
}

func TestXingTuVideoBodyLimitAllowsExactBoundaryAndIsolatesLegacyRoutes(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/v1/videos", XingTuVideoBodyLimit(), func(c *gin.Context) {
		var body bytes.Buffer
		_, _ = body.ReadFrom(c.Request.Body)
		c.String(http.StatusOK, "%d", body.Len())
	})

	for _, test := range []struct {
		name     string
		version  string
		size     int
		expected int
	}{
		{name: "current exact boundary", version: "xtai-video-billing-v2.1", size: XingTuVideoMaxRequestBytes, expected: http.StatusOK},
		{name: "legacy create is also bounded", version: "xtai-video-billing-v2", size: XingTuVideoMaxRequestBytes + 1, expected: http.StatusRequestEntityTooLarge},
		{name: "unknown XingTu contract is bounded", version: "xtai-video-billing-future", size: XingTuVideoMaxRequestBytes + 1, expected: http.StatusRequestEntityTooLarge},
		{name: "ordinary OpenAI route unchanged", version: "", size: XingTuVideoMaxRequestBytes + 1, expected: http.StatusOK},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, "/v1/videos", bytes.NewReader(bytes.Repeat([]byte("x"), test.size)))
			request.Header.Set("X-XingTu-Contract-Version", test.version)
			recorder := httptest.NewRecorder()
			router.ServeHTTP(recorder, request)
			require.Equal(t, test.expected, recorder.Code)
		})
	}
}
