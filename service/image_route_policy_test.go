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
