package controller

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestXingTuTaskErrorRedactsProviderSpecificDetails(t *testing.T) {
	taskErr := &dto.TaskError{
		Code:       "paisio_private_error",
		Message:    "provider task private-123 rejected with credential detail",
		StatusCode: http.StatusBadGateway,
		Error:      errors.New("private"),
	}

	require.Equal(t, "video_service_unavailable", safeXingTuTaskErrorCode(taskErr))
	require.Equal(t, "video service temporarily failed", safeXingTuTaskErrorMessage(taskErr))
}

func TestXingTuTaskErrorPreservesStableLocalDebtCode(t *testing.T) {
	taskErr := &dto.TaskError{Code: "account_in_debt", StatusCode: http.StatusForbidden}
	require.Equal(t, "account_in_debt", safeXingTuTaskErrorCode(taskErr))
}

func TestXingTuVideoProxyErrorRedactsUpstreamAndDisablesSharedCaching(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodGet, "/v1/videos/task_public/content", nil)
	context.Request.Header.Set(service.XingTuVideoContractHeader, service.XingTuVideoContractV2)
	context.Params = gin.Params{{Key: "task_id", Value: "task_public"}}
	context.Set("xingtu_request_id", "req_proxy_0001")

	videoProxyError(context, http.StatusBadGateway, "server_error", "paisio private URL failed")
	require.Contains(t, recorder.Body.String(), `"code":"video_result_unavailable"`)
	require.NotContains(t, recorder.Body.String(), "paisio")

	cacheRecorder := httptest.NewRecorder()
	cacheContext, _ := gin.CreateTestContext(cacheRecorder)
	cacheContext.Request = context.Request
	setVideoCacheControl(cacheContext)
	require.Equal(t, "private, no-store", cacheRecorder.Header().Get("Cache-Control"))
}
