package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestAbortWithOpenAIMessageUsesXingTuErrorContractForVideoV2(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/v1/videos", nil)
	context.Request.Header.Set(service.XingTuVideoContractHeader, service.XingTuVideoContractV2)
	context.Request.Header.Set("Idempotency-Key", "req_auth_0001")

	abortWithOpenAiMessage(context, http.StatusUnauthorized, "invalid token")

	require.Equal(t, http.StatusUnauthorized, recorder.Code)
	require.JSONEq(t, `{
		"error": {
			"code": "authentication_failed",
			"message": "invalid token",
			"request_id": "req_auth_0001",
			"retryable": false
		}
	}`, recorder.Body.String())
}
