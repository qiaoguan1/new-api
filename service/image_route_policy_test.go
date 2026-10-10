package service

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/types"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func imageContext(path, body string) *gin.Context {
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	c.Request.Header.Set("Content-Type", "application/json")
	return c
}

func TestImageRouteRejectsWrongEndpointBeforeSelectingNextChannel(t *testing.T) {
	c := imageContext("/v1/images/edits", `{"size":"1024x1024"}`)
	setting := `{"image_endpoints":["/v1/images/generations"]}`
	first, second := &model.Channel{Id: 991, Setting: &setting}, &model.Channel{Id: 992}
	calls := 0
	channel, err := SelectImageRoute(c, "gpt-image-2", nil, func(excluded map[int]bool) (*model.Channel, error) {
		calls++
		if !excluded[first.Id] {
			return first, nil
		}
		return second, nil
	})
	require.NoError(t, err)
	require.Equal(t, 992, channel.Id)
	require.Equal(t, 2, calls)
	require.True(t, ImageChannelAllowed(imageContext("/v1/responses", ""), first, "gpt-6"))
}

func TestImageCapabilitySizeDoesNotConsumeRequestBody(t *testing.T) {
	c := imageContext("/v1/images/generations", `{"size":"2048x2048"}`)
	setting := `{"image_sizes":["1024x1024"]}`
	channel := &model.Channel{Id: 990, Setting: &setting}
	require.False(t, ImageChannelAllowed(c, channel, "gpt-image-2"))
	// Repeat filtering observes the same requested size, not an exhausted body.
	require.False(t, ImageChannelAllowed(c, channel, "gpt-image-2"))
}

func TestImageSafeFailoverDoesNotReplayUnknownSubmission(t *testing.T) {
	for _, status := range []int{408, 425, 500, 502, 503, 504, 524} {
		err := types.NewErrorWithStatusCode(errors.New("upstream timed out"), types.ErrorCodeBadResponseStatusCode, status)
		require.False(t, IsSafeImageRejection(err), "status %d", status)
	}
	require.False(t, IsSafeImageRejection(types.NewError(errors.New("connection reset"), types.ErrorCodeDoRequestFailed)))
	require.False(t, IsSafeImageRejection(types.NewErrorWithStatusCode(errors.New("unknown rate limit outcome"), types.ErrorCodeBadResponseStatusCode, 429)))
	require.True(t, IsSafeImageRejection(types.NewErrorWithStatusCode(errors.New("no available image quota"), types.ErrorCodeBadResponseStatusCode, 429)))
}

func TestImagePaymentRequiredFailoverNeedsDefiniteQuotaRejection(t *testing.T) {
	for _, message := range []string{"insufficient quota", "insufficient balance", "no available image quota"} {
		err := types.NewErrorWithStatusCode(errors.New(message), types.ErrorCodeBadResponseStatusCode, http.StatusPaymentRequired)
		require.True(t, IsSafeImageRejection(err), "message %q", message)
	}
	for _, message := range []string{"payment required", "charge pending", "resolution_requires_other_route"} {
		err := types.NewErrorWithStatusCode(errors.New(message), types.ErrorCodeBadResponseStatusCode, http.StatusPaymentRequired)
		require.False(t, IsSafeImageRejection(err), "message %q", message)
	}
}

func TestImagePaymentRequiredQuotaCooldownDoesNotDisableOtherModels(t *testing.T) {
	c := imageContext("/v1/images/generations", `{}`)
	channel := &model.Channel{Id: 996}
	err := types.NewErrorWithStatusCode(errors.New("insufficient balance"), types.ErrorCodeBadResponseStatusCode, http.StatusPaymentRequired)
	RecordImageRouteRejection(c, channel.Id, "banana-flash", err)
	require.True(t, imageRequestExclusions(c)[channel.Id])
	require.False(t, ImageChannelAllowed(imageContext("/v1/images/generations", `{}`), channel, "banana-flash"))
	require.True(t, ImageChannelAllowed(imageContext("/v1/images/generations", `{}`), channel, "banana-pro"))
	require.True(t, ImageChannelAllowed(imageContext("/v1/responses", `{}`), channel, "gpt-5.5"))
}

func TestImageQuotaCooldownIsScopedToModelAndEndpoint(t *testing.T) {
	c := imageContext("/v1/images/generations", "{}")
	channel := &model.Channel{Id: 993}
	err := types.NewErrorWithStatusCode(errors.New("no available image quota"), types.ErrorCodeBadResponseStatusCode, 429)
	RecordImageRouteRejection(c, channel.Id, "gpt-image-2", err)
	require.False(t, ImageChannelAllowed(c, channel, "gpt-image-2"))
	require.True(t, ImageChannelAllowed(c, channel, "gpt-image-2.5"))
	require.True(t, ImageChannelAllowed(imageContext("/v1/images/edits", "{}"), channel, "gpt-image-2"))
	require.True(t, ImageChannelAllowed(imageContext("/v1/responses", "{}"), channel, "gpt-5.5"))
}

func TestImageSpecRejectionDoesNotCooldownValidNewRequests(t *testing.T) {
	c := imageContext("/v1/images/generations", `{"size":"4096x4096"}`)
	channel := &model.Channel{Id: 995}
	err := types.NewErrorWithStatusCode(errors.New("resolution_requires_other_route"), types.ErrorCodeBadResponseStatusCode, 429)
	RecordImageRouteRejection(c, channel.Id, "gpt-image-2", err)
	require.True(t, imageRequestExclusions(c)[channel.Id])
	valid := imageContext("/v1/images/generations", `{"size":"1024x1024"}`)
	require.True(t, ImageChannelAllowed(valid, channel, "gpt-image-2"))
}

func TestImageLocalBackupErrorIsNotAnUnknownSubmission(t *testing.T) {
	c := imageContext("/v1/images/generations", "{}")
	err := types.NewErrorWithStatusCode(errors.New("local proxy initialization failed"), types.ErrorCodeDoRequestFailed, 500)
	c.Set("xtai_image_submit_started", false)
	require.Same(t, err, NormalizeImageSubmissionError(c, err))
	c.Set("xtai_image_submit_started", true)
	unknown := NormalizeImageSubmissionError(c, err)
	require.Equal(t, types.ErrorCode("image_submit_uncertain"), unknown.GetErrorCode())
	require.True(t, types.IsSkipRetryError(unknown))
}

func TestImageAdapterUnconfirmedResultCannotMasqueradeAsSafeHTTPRejection(t *testing.T) {
	for _, code := range []types.ErrorCode{"upstream_outcome_unconfirmed", "upstream_no_image", "invalid_result", "result_fetch_failed"} {
		for _, status := range []int{http.StatusBadRequest, http.StatusUnauthorized, http.StatusForbidden, http.StatusNotFound, http.StatusBadGateway} {
			c := imageContext("/v1/images/generations", `{}`)
			c.Set("xtai_image_submit_started", true)
			err := types.NewErrorWithStatusCode(errors.New("Adapter could not confirm the generation result"), code, status)
			require.False(t, IsSafeImageRejection(err), "code %q status %d", code, status)
			unknown := NormalizeImageSubmissionError(c, err)
			require.Equal(t, types.ErrorCode("image_submit_uncertain"), unknown.GetErrorCode())
			require.Equal(t, http.StatusBadGateway, unknown.StatusCode)
			require.Same(t, err, unknown.Unwrap())
			require.Equal(t, status, err.StatusCode)
			require.True(t, types.IsSkipRetryError(unknown))
			require.Equal(t, "uncertain", c.Writer.Header().Get("X-XingTu-Image-Submission-State"))
		}
	}
}

func TestImageQuotaMarkersCannotOverrideNoRetryOrViolationFee(t *testing.T) {
	for _, err := range []*types.NewAPIError{
		types.NewErrorWithStatusCode(errors.New("insufficient quota"), types.ErrorCodeBadResponseStatusCode, http.StatusPaymentRequired, types.ErrOptionWithSkipRetry()),
		types.NewErrorWithStatusCode(errors.New("insufficient balance"), types.ErrorCodeViolationFeeGrokCSAM, http.StatusPaymentRequired),
	} {
		require.False(t, IsSafeImageRejection(err))
	}
}

func TestImageForbiddenRequiresExplicitNoTaskProof(t *testing.T) {
	for _, message := range []string{"permission denied", "policy blocked", "unknown forbidden response"} {
		c := imageContext("/v1/images/generations", `{}`)
		c.Set("xtai_image_submit_started", true)
		err := types.NewErrorWithStatusCode(errors.New(message), types.ErrorCodeBadResponseStatusCode, http.StatusForbidden)
		require.False(t, IsSafeImageRejection(err), "message %q", message)
		unknown := NormalizeImageSubmissionError(c, err)
		require.Equal(t, types.ErrorCode("image_submit_uncertain"), unknown.GetErrorCode())
		require.Equal(t, http.StatusBadGateway, unknown.StatusCode)
		require.True(t, types.IsSkipRetryError(unknown))
	}
	err := types.NewErrorWithStatusCode(errors.New("upstream_rejected_no_task: model permission rejected before generation"), types.ErrorCodeBadResponseStatusCode, http.StatusForbidden)
	require.True(t, IsSafeImageRejection(err))
}

func TestImageTaskMetadataOverridesQuotaOrNoTaskMessage(t *testing.T) {
	for _, metadata := range []string{`{"task_id":"original-task"}`, `{"job_id":"original-job"}`, `{"data":{"taskId":"original-task"}}`, `{"task":{"jobId":"original-job"}}`} {
		err := types.WithOpenAIError(types.OpenAIError{Message: "upstream_rejected_no_task: insufficient quota", Code: "upstream_rejected_no_task", Metadata: []byte(metadata)}, http.StatusPaymentRequired)
		require.False(t, IsSafeImageRejection(err), "metadata %s", metadata)
		c := imageContext("/v1/images/generations", `{}`)
		c.Set("xtai_image_submit_started", true)
		unknown := NormalizeImageSubmissionError(c, err)
		require.Equal(t, http.StatusBadGateway, unknown.StatusCode)
		require.Equal(t, types.ErrorCode("image_submit_uncertain"), unknown.GetErrorCode())
		require.True(t, types.IsSkipRetryError(unknown))
		require.Same(t, err, unknown.Unwrap())
		require.Equal(t, metadata, string(err.Metadata))
	}
	for _, metadata := range []string{`{"request_id":"relay-only","id":"request-only","uuid":"request-correlation"}`, `{"task_id":"","data":{"jobId":""}}`} {
		err := types.WithOpenAIError(types.OpenAIError{Message: "insufficient quota", Code: "quota_rejection", Metadata: []byte(metadata)}, http.StatusPaymentRequired)
		require.True(t, IsSafeImageRejection(err), "request metadata must not be guessed as task identity: %s", metadata)
	}
}

func TestImagePreSubmissionFailuresAndTextAuthKeepOriginalHTTPStatus(t *testing.T) {
	for _, code := range []types.ErrorCode{types.ErrorCodeAccessDenied, "upstream_outcome_unconfirmed"} {
		c := imageContext("/v1/images/generations", `{}`)
		c.Set("xtai_image_submit_started", false)
		err := types.NewErrorWithStatusCode(errors.New("not sent"), code, http.StatusForbidden)
		require.Same(t, err, NormalizeImageSubmissionError(c, err))
		require.Equal(t, http.StatusForbidden, err.StatusCode)
	}
	c := imageContext("/v1/chat/completions", `{}`)
	err := types.NewErrorWithStatusCode(errors.New("login required"), types.ErrorCodeAccessDenied, http.StatusUnauthorized)
	require.Same(t, err, NormalizeImageSubmissionError(c, err))
	require.Equal(t, http.StatusUnauthorized, err.StatusCode)
}

func TestImageCallerACLAndViolationChargeCannotBecomeRouteFallback(t *testing.T) {
	c := imageContext("/v1/images/generations", `{}`)
	caller := types.NewErrorWithStatusCode(errors.New("upstream_rejected_no_task: caller model access denied"), types.ErrorCodeAccessDenied, http.StatusForbidden)
	require.False(t, IsSafeImageRejection(caller))
	for _, submitted := range []bool{false, true} {
		c.Set("xtai_image_submit_started", submitted)
		require.Same(t, caller, NormalizeImageSubmissionError(c, caller))
		require.Equal(t, http.StatusForbidden, caller.StatusCode)
	}

	fee := types.NewErrorWithStatusCode(errors.New("violation fee already identified"), types.ErrorCodeViolationFeeGrokCSAM, http.StatusForbidden, types.ErrOptionWithSkipRetry())
	c.Set("xtai_image_submit_started", true)
	require.False(t, IsSafeImageRejection(fee))
	require.Same(t, fee, NormalizeImageSubmissionError(c, fee))
	require.Equal(t, types.ErrorCodeViolationFeeGrokCSAM, fee.GetErrorCode())
	require.True(t, types.IsSkipRetryError(fee))
}
